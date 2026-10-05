"""The Tractor job for one Send, built from plain values so it reads without Houdini.

Each layer renders every frame, then optionally denoises and encodes them:

    <layer>
    ├── Render <layer>      one task per frame
    ├── Denoise <layer>     one task per frame, after the frames it reads
    ├── Encode <layer>      after every frame is final
    └── Cleanup <layer>     after everything else: checks, deletes tmp/, marks complete

Every task is one command from `commands.py`. Titles carry the layer, so they
are unique within the job, which Instances rely on.
"""

from __future__ import annotations

import tractor.api.author as author

from pipe.dcc.houdini.tractor import commands
from pipe.dcc.houdini.tractor.commands import Layer

# Tractor runs every command on blades that offer this service.
SERVICE = "EL9"

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


def _task(title: str, script: str, retries: list[int] | None = None) -> author.Task:
    task = author.Task(title=title)
    command = author.Command(argv=["/bin/bash", "-exc", script], service=SERVICE)
    if retries:
        command.retryrc = retries
    task.addCommand(command)
    return task
