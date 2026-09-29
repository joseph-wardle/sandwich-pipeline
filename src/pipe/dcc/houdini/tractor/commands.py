"""The command of each task in the job, as the bash script a blade run"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from pipe.core.playblast.encoding import timecode
from pipe.core.playblast.presets import FFmpegPreset
from pipe.core.render import (
    COMPLETE,
    DENOISE_CONFIG,
    DENOISED,
    ENCODE,
    RENDER_USD,
    TMP,
)

LICENSE_SERVER = "animlic.cs.byu.edu"
# husk exits 3 without a license; point hserver at the license server before Tractor retries.
LICENSE_TRAP = rf'trap "test \$? -eq 3 && hserver -S {LICENSE_SERVER} && exit 3" EXIT'
# RenderMan's denoiser reads this many frames either side of each frame.
DENOISE_RADIUS = 3


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


def render(layer: Layer, frame: int) -> str:
    # husk makes no output folders, and exits 0 having written nothing.
    mkdir = shlex.join(["mkdir", "-p", *map(str, layer.render.folders)])
    husk = ["husk", "--frame", str(frame), *layer.render.husk]
    husk.append(str(layer.folder / RENDER_USD))
    return f"{LICENSE_TRAP} && {mkdir} && {shlex.join(husk)}"


def denoise_window(frame: int, frames: list[int]) -> list[int]:
    """The frames the denoise of `frame` reads."""
    # Neighbours outside the rendered range don't exist.
    start = max(frames[0], frame - DENOISE_RADIUS)
    end = min(frames[-1], frame + DENOISE_RADIUS)
    return list(range(start, end + 1))


def denoise(layer: Layer, product: str, frame: int) -> str:
    """denoise_batch exits 0 even when it writes nothing, so the output check
    shares its command: a Tractor retry then re-runs both, never the check alone.
    """
    folder = layer.folder
    window = denoise_window(frame, layer.frames)
    raw = folder / TMP / product
    denoised = folder / TMP / DENOISED / f"{frame:04}.exr"
    final = folder / product / f"{frame:04}.exr"
    config = folder / DENOISE_CONFIG
    exclude = []
    if window[0] < frame:
        exclude.append(f"{window[0]}-{frame - 1}")
    if frame < window[-1]:
        exclude.append(f"{frame + 1}-{window[-1]}")
    inputs = [config, *(raw / f"{f:04}.exr" for f in window)]
    q = shlex.quote
    return "\n".join(
        [
            f"export FrameInclude={window[0]}-{window[-1]}",
            f"export FrameExclude={','.join(exclude) or -1}",
            f"export InputFile={q(str(raw / '####.exr'))}",
            f"export OutputFile={q(str(denoised.parent / '####.exr'))}",
            f"for f in {' '.join(q(str(p)) for p in inputs)}; do",
            '    if [ ! -f "$f" ]; then echo "Denoise input is missing: $f" >&2; exit 1; fi',
            "done",
            f"mkdir -p {q(str(denoised.parent))} {q(str(final.parent))}",
            f"out={q(str(denoised))}",
            'rm -f "$out"',
            f'"$RMANTREE/bin/denoise_batch" --json {q(str(config))}',
            '[ -f "$out" ] || { echo "denoise_batch exited without writing $out" >&2; exit 1; }',
            # Written aside and renamed in, so the folder only ever holds whole frames.
            f'mv -f "$out" {q(str(final))}',
        ]
    )


def encode(layer: Layer, spec: Encode) -> str:
    """Convert the frames to display PNGs, then assemble them into the video."""
    q = shlex.quote
    frames = layer.frames
    scratch = layer.folder / TMP / ENCODE
    first = q(str(spec.images / f"{frames[0]:04}.exr"))
    video = scratch / spec.video.name
    return "\n".join(
        [
            f"rm -rf {q(str(scratch))}",
            f"mkdir -p {q(str(scratch))} {q(str(spec.video.parent))}",
            # Assigned alone, so a failed read stops the script under -e.
            f"info=$(hoiiotool --info -v {first})",
            'list=", $(sed -n \'s/^ *channel list: //p\' <<< "$info"),"',
            "found=0",
            f"for ch in {' '.join(spec.channels)}; do",
            "    found=1",
            '    for c in ${ch//,/ }; do [[ "$list" == *", $c,"* || "$list" == *", $c ("* ]] || found=0; done',
            '    if [ "$found" = 1 ]; then break; fi',
            "done",
            f'if [ "$found" = 0 ]; then echo "No beauty channels in "{first}": $list" >&2; exit 1; fi',
            f"for n in {' '.join(f'{frame:04}' for frame in frames)}; do",
            f"    hoiiotool --colorconfig {q(spec.colorconfig)}"
            f' {q(str(spec.images))}/$n.exr --ch "$ch"'
            f" --ociodisplay:from=scene_linear {q(spec.display)} {q(spec.view)}"
            f" -d uint16 -o {q(str(scratch))}/$n.png",
            "done",
            f"ffmpeg -y -framerate {spec.frame_rate:g}"
            f" -pattern_type glob -i {q(f'{scratch}/*.png')}"
            f" {shlex.join(spec.preset.args())}"
            f" -timecode {timecode(frames[0], round(spec.frame_rate))}"
            f" {q(str(video))}",
            f"mv -f {q(str(video))} {q(str(spec.video))}",
        ]
    )


def cleanup(layer: Layer) -> str:
    """Check that every final exists, then delete tmp/ and mark the folder complete."""
    q = shlex.quote
    spec = layer.encode
    removed = spec.images if spec and spec.remove_frames else None
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
    if spec:
        lines.append(f"[ -f {q(str(spec.video))} ] || missing+=({q(str(spec.video))})")
    lines += [
        'if [ "${#missing[@]}" -gt 0 ]; then',
        '    printf "Missing: %s\\n" "${missing[@]}" >&2',
        '    echo "Cleanup deleted nothing, since the files above are missing." >&2',
        "    exit 1",
        "fi",
        f"rm -rf {q(str(layer.folder / TMP))}",
    ]
    if removed:
        lines.append(f"rm -rf {q(str(removed))}")
    lines.append(f"touch {q(str(layer.folder / COMPLETE))}")
    return "\n".join(lines)
