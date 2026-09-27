"""The Tractor job for one Send, built from plain values so it reads without Houdini.

Each layer renders every frame, then optionally denoises and encodes them:

    <layer>
    ├── Render <layer>      one task per frame (per tile when tiled)
    ├── Denoise <layer>     one task per frame, after the frames it reads
    └── Encode <layer>      after every frame is final

Every task is one command that a retry can run again on any blade. Titles
carry the layer, so they are unique within the job, which Instances rely on.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

import tractor.api.author as author

from pipe.dcc.houdini.tractor import denoise, paths

# Tractor retries these exit codes on its own.
RENDER_RETRIES = [
    -11,  # Segmentation fault
    -9,  # Unknown
    3,  # Can't get license
    135,  # Bus error
    139,  # Segmentation fault
]
DENOISE_RETRIES = [
    -11,  # Segmentation fault
    139,  # Segmentation fault
]
# husk exits 3 without a license; point hserver at the license server before Tractor retries.
LICENSE_TRAP = (
    r'trap "test \$? -eq 3 && hserver -S $HOUDINI_LICENSE_SERVER && exit 3" EXIT'
)

# The frames hold sRGB display pixels from OCIO, so the movie says so and
# players show it as the Houdini viewer did
SRGB_TAGS = "-colorspace bt709 -color_primaries bt709 -color_trc iec61966-2-1"
# Browsers need even dimensions, limited-range BT.709 YUV and the index up front
WEB_FLAGS = (
    "-vf 'scale=trunc(iw/2)*2:trunc(ih/2)*2:out_color_matrix=bt709:out_range=tv' "
    "-movflags +faststart"
)


@dataclass(frozen=True)
class Render:
    # husk's options besides the frame and tile it renders
    husk: list[str]
    folders: list[Path]
    tiles: int  # 0 renders whole frames
    timelimit: int  # seconds, 0 for none
    service: str


@dataclass(frozen=True)
class Denoise:
    product: str
    topology: denoise.Topology
    service: str


@dataclass(frozen=True)
class Encode:
    images: Path
    # Candidate beauty channel sets, the first one the frames have is used.
    channels: list[str]
    video: Path
    framerate: int
    colorconfig: str
    display: str
    view: str
    codec: str
    quality: int
    service: str


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
    last = _render_task(layer)
    task.addChild(last)
    if layer.denoise:
        last = _denoise_task(layer, layer.denoise)
        task.addChild(last)
    if layer.encode:
        encode = _task(
            f"Encode {layer.name}",
            _encode_script(layer.folder, layer.encode, layer.frames),
            layer.encode.service,
        )
        encode.addChild(author.Instance(title=last.title))
        task.addChild(encode)
    return task


def _render_title(layer: Layer, frame: int) -> str:
    return f"Render {layer.name} {frame}"


def _render_task(layer: Layer) -> author.Task:
    render = layer.render
    # husk makes no output folders, and exits 0 having written nothing.
    mkdir = shlex.join(["mkdir", "-p", *map(str, render.folders)])
    task = author.Task(title=f"Render {layer.name}")
    for frame in layer.frames:
        frame_task = author.Task(title=_render_title(layer, frame))
        tiles = [
            author.Task(title=f"{frame_task.title} tile {tile}")
            for tile in range(render.tiles)
        ]
        for tile, tile_task in enumerate(tiles or [frame_task]):
            husk = ["husk", "--frame", str(frame), *render.husk]
            if render.tiles:
                husk += ["--tile-index", str(tile)]
            husk.append(str(layer.folder / paths.RENDER_USD))
            command = author.Command(
                argv=[
                    "/bin/bash",
                    "-exc",
                    f"{LICENSE_TRAP} && {mkdir} && {shlex.join(husk)}",
                ],
                retryrc=RENDER_RETRIES,
                service=render.service,
            )
            if render.timelimit:
                command.maxrunsecs = render.timelimit
            tile_task.addCommand(command)
            if tile_task is not frame_task:
                frame_task.addChild(tile_task)
        task.addChild(frame_task)
    return task


def _denoise_task(layer: Layer, spec: Denoise) -> author.Task:
    task = author.Task(title=f"Denoise {layer.name}")
    for frame in layer.frames:
        window = denoise.window(frame, layer.frames, spec.topology)
        script = denoise.script(layer.folder, spec.product, frame, window)
        frame_task = _task(
            f"Denoise {layer.name} {frame}", script, spec.service, DENOISE_RETRIES
        )
        for neighbour in window:
            frame_task.addChild(author.Instance(title=_render_title(layer, neighbour)))
        task.addChild(frame_task)
    return task


def _task(
    title: str, script: str, service: str, retries: list[int] | None = None
) -> author.Task:
    task = author.Task(title=title)
    command = author.Command(argv=["/bin/bash", "-exc", script], service=service)
    if retries:
        command.retryrc = retries
    task.addCommand(command)
    return task


def _encode_script(folder: Path, encode: Encode, frames: list[int]) -> str:
    """Convert the frames to display PNGs, then assemble them into the video.

    Beauty channel names vary with the product's vars, and hoiiotool fills a
    missing channel with black rather than failing, so the names are checked.
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
            f" -o {q(str(scratch))}/$n.png",
            "done",
            f"ffmpeg -y -framerate {encode.framerate}"
            f" -pattern_type glob -i {q(f'{scratch}/*.png')}"
            f" {_codec_flags(encode.codec, encode.quality)} {SRGB_TAGS} {WEB_FLAGS}"
            f" {q(str(video))}",
            f"mv -f {q(str(video))} {q(str(encode.video))}",
        ]
    )


def _codec_flags(codec: str, quality: int) -> str:
    if codec == "prores":
        return f"-c:v prores_ks -profile:v 3 -qscale:v {quality} -pix_fmt yuv422p10le"
    return f"-c:v libx264 -crf {quality} -pix_fmt yuv420p"
