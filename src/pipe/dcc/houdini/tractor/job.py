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
MONSTERS = "Monster_EL9"
# Monsters are blades nobody sits at, so a sim of hours is not killed when a
# student logs in.
BLADES = {"Monsters only": MONSTERS, "Any blade": SERVICE}
# The range and default of Tractor Submit's Priority.
PRIORITY = (0, 50, 10)

RENDER_RETRIES = [
    3,  # Can't get license
    134,  # Abort
    135,  # Bus error
    139,  # Segmentation fault
]
DENOISE_RETRIES = [
    139,  # Segmentation fault
]


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


def _render_title(layer: Layer, frame: int) -> str:
    return f"Render {layer.name} {frame}"


def _render_task(layer: Layer) -> author.Task:
    task = author.Task(title=f"Render {layer.name}")
    for frame in layer.frames:
        script = commands.render(layer, frame)
        task.addChild(_task(_render_title(layer, frame), script, RENDER_RETRIES))
    return task


def _denoise_task(layer: Layer, product: str) -> author.Task:
    task = author.Task(title=f"Denoise {layer.name}")
    for frame in layer.frames:
        script = commands.denoise(layer, product, frame)
        frame_task = _task(f"Denoise {layer.name} {frame}", script, DENOISE_RETRIES)
        for neighbour in commands.denoise_window(frame, layer.frames):
            frame_task.addChild(author.Instance(title=_render_title(layer, neighbour)))
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
    title: str, priority: int, service: str, spec: Wedges, names: list[str]
) -> author.Job:
    """A sim is never retried, as in `cache_job`. A flipbook is, when it
    couldn't draw."""
    job = author.Job(title=title, priority=priority)
    for name in names:
        finish = commands.wedge(spec, name, commands.WEDGE_FINISH)
        task = _task(f"Flipbook {name}", finish, service=service)
        # Each cache reads the ones written before it, and the flipbook all of them.
        task.serialsubtasks = True
        for path in spec.rebuilds:
            sim = commands.wedge(spec, name, commands.WEDGE_SIM, (path,))
            node = path.rpartition("/")[2]
            task.addChild(_task(f"Sim {name} {node}", sim, service=service))
        draw = author.Task(title=f"Draw {name}")
        for start, end in spec.chunks:
            frames = (str(start), str(end))
            script = commands.wedge(spec, name, commands.WEDGE_FLIPBOOK, frames)
            label = f"Draw {name} {start}-{end}"
            draw.addChild(_task(label, script, [commands.WEDGE_DRAW_FAILED], service))
        task.addChild(draw)
        job.addChild(task)
    return job


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
