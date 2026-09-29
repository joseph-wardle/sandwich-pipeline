"""The Tractor job for one Send, built from plain values so it reads without Houdini.

Each layer renders every frame, then optionally denoises and encodes them:

    <layer>
    ├── Render <layer>      one task per frame
    ├── Denoise <layer>     one task per frame, after the frames it reads
    ├── Encode <layer>      after every frame is final
    └── Cleanup <layer>     after everything else: checks, deletes tmp/, marks complete

Every task is one command that a retry can run again on any blade. Titles
carry the layer, so they are unique within the job, which Instances rely on.
Other tasks replace only their own outputs and scratch; only Cleanup deletes
what another task wrote.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

import tractor.api.author as author

from pipe.core.playblast.encoding import timecode
from pipe.core.playblast.presets import FFmpegPreset
from pipe.dcc.houdini.tractor import denoise, paths

# Tractor runs every command on blades that offer this service.
SERVICE = "EL9"

RENDER_RETRIES = [
    3,  # Can't get license
    135,  # Bus error
    139,  # Segmentation fault
]
DENOISE_RETRIES = [
    139,  # Segmentation fault
]
LICENSE_SERVER = "animlic.cs.byu.edu"
# husk exits 3 without a license; point hserver at the license server before Tractor retries.
LICENSE_TRAP = rf'trap "test \$? -eq 3 && hserver -S {LICENSE_SERVER} && exit 3" EXIT'


@dataclass(frozen=True)
class Render:
    # husk's options besides the frame it renders
    husk: list[str]
    folders: list[Path]


@dataclass(frozen=True)
class Denoise:
    product: str


@dataclass(frozen=True)
class Encode:
    images: Path
    # Candidate beauty channel sets, the first one the frames have is used.
    channels: list[str]
    video: Path
    colorconfig: str
    display: str
    view: str
    preset: FFmpegPreset
    frame_rate: float
    # Cleanup deletes the frames once the video is written.
    remove_frames: bool


@dataclass(frozen=True)
class Layer:
    name: str
    folder: Path
    frames: list[int]
    render: Render
    denoise: Denoise | None = None
    encode: Encode | None = None


def build(title: str, priority: int, envkey: str, layers: list[Layer]) -> author.Job:
    job = author.Job(title=title, priority=priority, envkey=[envkey])
    for layer in layers:
        job.addChild(layer_task(layer))
    return job


def layer_task(layer: Layer) -> author.Task:
    task = author.Task(title=layer.name)
    steps = [_render_task(layer)]
    if layer.denoise:
        steps.append(_denoise_task(layer, layer.denoise))
    if layer.encode:
        encode = _task(
            f"Encode {layer.name}",
            _encode_script(layer.folder, layer.encode, layer.frames),
        )
        encode.addChild(author.Instance(title=steps[-1].title))
        steps.append(encode)
    cleanup = _task(f"Cleanup {layer.name}", _cleanup_script(layer))
    for step in steps:
        task.addChild(step)
        cleanup.addChild(author.Instance(title=step.title))
    task.addChild(cleanup)
    return task


def _render_title(layer: Layer, frame: int) -> str:
    return f"Render {layer.name} {frame}"


def _render_task(layer: Layer) -> author.Task:
    render = layer.render
    # husk makes no output folders, and exits 0 having written nothing.
    mkdir = shlex.join(["mkdir", "-p", *map(str, render.folders)])
    task = author.Task(title=f"Render {layer.name}")
    for frame in layer.frames:
        husk = ["husk", "--frame", str(frame), *render.husk]
        husk.append(str(layer.folder / paths.RENDER_USD))
        script = f"{LICENSE_TRAP} && {mkdir} && {shlex.join(husk)}"
        task.addChild(_task(_render_title(layer, frame), script, RENDER_RETRIES))
    return task


def _denoise_task(layer: Layer, spec: Denoise) -> author.Task:
    task = author.Task(title=f"Denoise {layer.name}")
    for frame in layer.frames:
        window = denoise.window(frame, layer.frames)
        script = denoise.script(layer.folder, spec.product, frame, window)
        frame_task = _task(f"Denoise {layer.name} {frame}", script, DENOISE_RETRIES)
        for neighbour in window:
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


def _encode_script(folder: Path, encode: Encode, frames: list[int]) -> str:
    """Convert the frames to display PNGs, then assemble them into the video.

    Beauty channel names vary with the product's vars, and hoiiotool fills a
    missing channel with black rather than failing, so the names are checked.
    The PNGs are 16-bit, so Master keeps its 10 bits.
    """
    q = shlex.quote
    scratch = folder / paths.TMP / paths.ENCODE
    first = q(str(encode.images / f"{frames[0]:04}.exr"))
    video = scratch / encode.video.name
    return "\n".join(
        [
            f"rm -rf {q(str(scratch))}",
            f"mkdir -p {q(str(scratch))} {q(str(encode.video.parent))}",
            # Assigned alone, so a failed read stops the script under -e.
            f"info=$(hoiiotool --info -v {first})",
            'list=", $(sed -n \'s/^ *channel list: //p\' <<< "$info"),"',
            "found=0",
            f"for ch in {' '.join(encode.channels)}; do",
            "    found=1",
            '    for c in ${ch//,/ }; do [[ "$list" == *", $c,"* || "$list" == *", $c ("* ]] || found=0; done',
            '    if [ "$found" = 1 ]; then break; fi',
            "done",
            f'if [ "$found" = 0 ]; then echo "No beauty channels in "{first}": $list" >&2; exit 1; fi',
            f"for n in {' '.join(f'{frame:04}' for frame in frames)}; do",
            f"    hoiiotool --colorconfig {q(encode.colorconfig)}"
            f' {q(str(encode.images))}/$n.exr --ch "$ch"'
            f" --ociodisplay:from=scene_linear {q(encode.display)} {q(encode.view)}"
            f" -d uint16 -o {q(str(scratch))}/$n.png",
            "done",
            f"ffmpeg -y -framerate {encode.frame_rate:g}"
            f" -pattern_type glob -i {q(f'{scratch}/*.png')}"
            f" {shlex.join(encode.preset.args())}"
            f" -timecode {timecode(frames[0], round(encode.frame_rate))}"
            f" {q(str(video))}",
            f"mv -f {q(str(video))} {q(str(encode.video))}",
        ]
    )


def _cleanup_script(layer: Layer) -> str:
    """Check that every final exists, then delete tmp/ and mark the folder complete.

    Tractor counts a skipped task as done, so the files are checked rather than
    the tasks trusted. Frames that are deleted are not checked: Send keeps their
    video in the new version folder, so it exists only if Encode read every one,
    and a retry after a partial delete still passes. A frame re-rendered after
    Encode needs Encode rerun before Cleanup.
    """
    q = shlex.quote
    encode = layer.encode
    removed = encode.images if encode and encode.remove_frames else None
    # Every output ends in the folder named after its prim, even one that
    # renders into tmp/ to be denoised.
    finals = [layer.folder / f.name for f in layer.render.folders]
    quoted = [q(str(f)) for f in finals if f != removed]
    lines = ["missing=()"]
    if quoted:
        lines += [
            f"for d in {' '.join(quoted)}; do",
            f"    for n in {' '.join(f'{frame:04}' for frame in layer.frames)}; do",
            '        [ -f "$d/$n.exr" ] || missing+=("$d/$n.exr")',
            "    done",
            "done",
        ]
    if encode:
        lines.append(
            f"[ -f {q(str(encode.video))} ] || missing+=({q(str(encode.video))})"
        )
    lines += [
        'if [ "${#missing[@]}" -gt 0 ]; then',
        '    printf "Missing: %s\\n" "${missing[@]}" >&2',
        '    echo "Cleanup deleted nothing, since the files above are missing." >&2',
        "    exit 1",
        "fi",
        f"rm -rf {q(str(layer.folder / paths.TMP))}",
    ]
    if removed:
        lines.append(f"rm -rf {q(str(removed))}")
    lines.append(f"touch {q(str(layer.folder / paths.COMPLETE))}")
    return "\n".join(lines)
