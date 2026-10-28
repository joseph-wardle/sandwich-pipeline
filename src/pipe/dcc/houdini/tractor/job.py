"""The Tractor job for one Send, built from plain values so it reads without Houdini.

A render Send's job has one task per layer. Each layer renders every frame,
then optionally denoises and encodes them:

    <layer>
    ├── Render <layer>      one task per frame
    ├── Denoise <layer>     one task per frame, after the frames it reads
    ├── Encode <layer>      after every frame is final
    └── Cleanup <layer>     after everything else: checks, deletes tmp/, marks complete

A cache Send's job writes each cache in turn, one task per simulation and
one per chunk of frames otherwise:

    Cache <node>            for each Cache first cache, upstream first
    Cache <node>            the cache that was sent

A wedge Send's job sims each wedge, flipbooks it a chunk of frames per task,
then finishes the flipbook:

    Flipbook w01            after all of its children, in order
    ├── Sim w01 <node>      one per cache the wedge writes, upstream first
    ├── Sim w01 <node>
    └── Draw w01
        ├── Draw w01 1001-1030
        └── Draw w01 1031-1060

When the wedges also render, each renders and denoises as a layer does once
its sims are written, then burns its frames:

    Burn w01                after all of its children, in order
    ├── Prepare w01         writes render.usd, after the wedge's sims
    ├── Render w01
    └── Denoise w01

Every task is one command from `commands.py`. Titles carry the layer, so they
are unique within the job, which Instances rely on.
"""

from __future__ import annotations

import tractor.api.author as author

from pipe.dcc.houdini.tractor import commands
from pipe.dcc.houdini.tractor.commands import Cache, Layer, Wedges

# Tractor runs a command only on blades that offer its service. Every blade
# offers EL9, and each offers the name of its profile.
SERVICE = "EL9"
# Monsters are blades nobody sits at, so a sim of hours is not killed when a
# student logs in. The New Monsters have their own profile.
MONSTERS = "Monster_EL9 || New_Monster_EL9"
BLADES = {"Monsters only": MONSTERS, "Any blade": SERVICE}
PRIORITY = (0, 50, 30)

RENDER_RETRIES = [
    3,  # Can't get license
    134,  # Abort
    135,  # Bus error
    137,  # Killed: out of memory on that blade
    139,  # Segmentation fault
]
DENOISE_RETRIES = [
    139,  # Segmentation fault
]
WEDGE_CHUNK = 10


def build(title: str, priority: int, envkey: str, layers: list[Layer]) -> author.Job:
    job = author.Job(title=title, priority=priority, envkey=[envkey])
    for layer in layers:
        job.addChild(layer_task(layer))
    return job


def layer_task(layer: Layer) -> author.Task:
    task = author.Task(title=layer.name)
    steps = [_render_task(layer)]
    if layer.denoise:
        steps.append(_denoise_task(layer, layer.denoise.product))
    if layer.encode:
        encode = _task(f"Encode {layer.name}", commands.encode(layer, layer.encode))
        encode.addChild(author.Instance(title=steps[-1].title))
        steps.append(encode)
    cleanup = _task(f"Cleanup {layer.name}", commands.cleanup(layer))
    for step in steps:
        task.addChild(step)
        cleanup.addChild(author.Instance(title=step.title))
    task.addChild(cleanup)
    return task


def _chunks(layer: Layer, size: int) -> list[list[int]]:
    return [layer.frames[i : i + size] for i in range(0, len(layer.frames), size)]


def _render_title(layer: Layer, frames: list[int]) -> str:
    span = f"{frames[0]}" if len(frames) == 1 else f"{frames[0]}-{frames[-1]}"
    return f"Render {layer.name} {span}"


def _render_task(layer: Layer, service: str = SERVICE, chunk: int = 1) -> author.Task:
    task = author.Task(title=f"Render {layer.name}")
    for frames in _chunks(layer, chunk):
        script = commands.render(layer, frames)
        title = _render_title(layer, frames)
        task.addChild(_task(title, script, RENDER_RETRIES, service))
    return task


def _denoise_task(
    layer: Layer, product: str, service: str = SERVICE, chunk: int = 1
) -> author.Task:
    rendered_by = {
        frame: _render_title(layer, frames)
        for frames in _chunks(layer, chunk)
        for frame in frames
    }
    task = author.Task(title=f"Denoise {layer.name}")
    for frame in layer.frames:
        script = commands.denoise(layer, product, frame)
        title = f"Denoise {layer.name} {frame}"
        frame_task = _task(title, script, DENOISE_RETRIES, service)
        window = commands.denoise_window(frame, layer.frames)
        # Neighbours in one chunk wait on the same Render task.
        for render in dict.fromkeys(rendered_by[neighbour] for neighbour in window):
            frame_task.addChild(author.Instance(title=render))
        task.addChild(frame_task)
    return task


def cache_job(
    title: str, priority: int, service: str, caches: list[Cache]
) -> author.Job:
    """No task is retried: a sim's retry starts again from its first frame,
    which can cost hours, so the artist decides whether to send it again."""
    job = author.Job(title=title, priority=priority, serialsubtasks=True)
    for cache in caches:
        name = f"Cache {cache.node}"
        if len(cache.chunks) == 1:
            start, end = cache.chunks[0]
            job.addChild(
                _task(name, commands.cache(cache, start, end), service=service)
            )
            continue
        task = author.Task(title=name)
        for start, end in cache.chunks:
            script = commands.cache(cache, start, end)
            task.addChild(_task(f"{name} {start:g}-{end:g}", script, service=service))
        job.addChild(task)
    return job


def wedge_job(
    title: str,
    priority: int,
    service: str,
    spec: Wedges,
    names: list[str],
    renders: dict[str, Layer],
    envkey: str,
) -> author.Job:
    """A sim is never retried, as in `cache_job`. A flipbook is, when it
    couldn't draw. `renders` holds the layer of each wedge that renders, whose
    Render and Denoise tasks take their environment from `envkey`, as a render
    Send's do."""
    job = author.Job(title=title, priority=priority, envkey=[envkey])
    for name in names:
        finish = commands.wedge(spec, name, commands.WEDGE_FINISH)
        task = _task(f"Flipbook {name}", finish, service=service)
        # Each cache reads the ones written before it, and the flipbook all of them.
        task.serialsubtasks = True
        for path in spec.rebuilds:
            sim = commands.wedge(spec, name, commands.WEDGE_SIM, (path,))
            task.addChild(_task(_sim_title(name, path), sim, service=service))
        draw = author.Task(title=f"Draw {name}")
        for start, end in spec.chunks:
            frames = (str(start), str(end))
            script = commands.wedge(spec, name, commands.WEDGE_FLIPBOOK, frames)
            label = f"Draw {name} {start}-{end}"
            draw.addChild(_task(label, script, [commands.WEDGE_DRAW_FAILED], service))
        task.addChild(draw)
        job.addChild(task)
        if name in renders:
            job.addChild(_wedge_render(spec, renders[name], service))
    return job


def _sim_title(name: str, path: str) -> str:
    return f"Sim {name} {path.rpartition('/')[2]}"


def _wedge_render(spec: Wedges, layer: Layer, service: str) -> author.Task:
    name = layer.name
    burn = commands.wedge(spec, name, commands.WEDGE_BURN)
    task = _task(f"Burn {name}", burn, service=service)
    task.serialsubtasks = True
    prepare = commands.wedge(spec, name, commands.WEDGE_PREPARE)
    prepare_task = _task(f"Prepare {name}", prepare, service=service)
    for path in spec.rebuilds:
        prepare_task.addChild(author.Instance(title=_sim_title(name, path)))
    task.addChild(prepare_task)
    task.addChild(_render_task(layer, service, WEDGE_CHUNK))
    if layer.denoise:
        product = layer.denoise.product
        task.addChild(_denoise_task(layer, product, service, WEDGE_CHUNK))
    return task


def _task(
    title: str,
    script: str,
    retries: list[int] | None = None,
    service: str = SERVICE,
) -> author.Task:
    task = author.Task(title=title)
    command = author.Command(argv=["/bin/bash", "-exc", script], service=service)
    if retries:
        command.retryrc = retries
    task.addCommand(command)
    return task
