from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import hou

from core.shot.version_adapter import houdini_department_stream, shot_owner_for
from core.versioning import (
    list_version_records,
    path_matches_stream,
    version_label as _format_version_label,
)

import core.playblast as _playblast_pkg

from dcc.houdini.hipfile.departments import DEPARTMENT_OPTIONS

if TYPE_CHECKING:
    from core.shotgrid import Shot

# Vendored TTF lives under `pipe/playblast/resources/fonts/`. Anchor on the
# `core.playblast` package itself so future moves of either module don't
# silently desync the relative jump.
_PLAYBLAST_PACKAGE_DIR = Path(_playblast_pkg.__file__).resolve().parent
HUD_FONT_PATH = (
    _PLAYBLAST_PACKAGE_DIR / "resources" / "fonts" / "LondrinaSolid-Regular.ttf"
)


def build_hud_filter_args(
    *,
    shot_code: str,
    artist_display_name: str,
    start_frame: int,
    resolution: tuple[int, int],
    now: datetime | None = None,
    version_label: str | None = None,
    title: str | None = None,
) -> list[dict[str, str]]:
    """Return one ffmpeg `drawtext` kwarg dict per HUD line.

    Caller chains them onto an ffmpeg input/filter graph:

        for kwargs in build_hud_filter_args(...):
            chain = chain.filter("drawtext", **kwargs)

    Layout: bottom-left stacks Artist (always, bottom row), Title (if
    given, middle), and Shot (with optional version suffix, top of the
    stack). Bottom-right stacks the date and a per-frame counter (Frame
    on the very bottom row). Sizing scales linearly with the output
    height so a 1080p export looks the same physical-percentage size as
    a 720p export.

    The frame counter uses the drawtext expression `Frame %{eif:n+<start>:d}`
    — `n` is the input image-2 frame index (always starting at 0), and
    `start_frame` is interpolated so negative-tail frame numbers display
    correctly. Colons inside the expression must reach ffmpeg as `\\:`;
    ffmpeg-python's filter-arg escaper rewrites bare `:` to `\\:`, so we
    intentionally do NOT pre-escape — pre-escaping would double-escape
    and produce a literal "\\:" on screen instead of an expression.
    """
    height = resolution[1]
    fontsize = round(height * 0.029)
    padding = round(height * 0.033)
    border_w = max(1, round(height * 0.0028))
    line_gap = round(height * 0.039)

    date_text = (now or datetime.now()).strftime("%Y-%m-%d")
    frame_text = f"Frame %{{eif:n+{start_frame}:d}}"

    common: dict[str, str] = {
        "fontfile": str(HUD_FONT_PATH),
        "fontsize": str(fontsize),
        "fontcolor": "white",
        "borderw": str(border_w),
        "bordercolor": "black",
    }

    # Bottom-left stack, bottom-up. Index 0 is the bottom row; each
    # subsequent line sits one `line_gap` above the previous.
    left_lines: list[str] = [f"Artist: {artist_display_name}"]
    if title:
        left_lines.append(f"Title: {title}")
    shot_line = f"Shot: {shot_code}"
    if version_label:
        shot_line += f" {version_label}"
    left_lines.append(shot_line)

    args: list[dict[str, str]] = []
    for index, text in enumerate(left_lines):
        args.append(
            {
                "text": text,
                "x": str(padding),
                "y": f"h-th-{padding + index * line_gap}",
                **common,
            }
        )

    # Bottom-right is unaffected: date above frame.
    args.append(
        {
            "text": date_text,
            "x": f"w-tw-{padding}",
            "y": f"h-th-{padding + line_gap}",
            **common,
        }
    )
    args.append(
        {
            "text": frame_text,
            "x": f"w-tw-{padding}",
            "y": f"h-th-{padding}",
            **common,
        }
    )

    return args


def resolve_current_hip_version(shot: Shot | None) -> tuple[str | None, str | None]:
    """Return `(version_label, title)` for the open HIP, or `(None, None)`.

    Returns `(None, None)` when the HIP is unsaved, lives outside the
    recognized shot/department layout, or has no saved version records
    in its manifest.

    The returned label is suffixed with `*` when the HIP has unsaved
    changes since its last `save_version`, so dailies reviewers can
    spot mid-iteration playblasts without the HUD lying about which
    versioned state they're based on.

    Note: this returns the *latest manifest record*, not the version
    embedded in the HIP filename. If an artist opens an older backup,
    the HUD still shows the highest version that was ever saved for
    that stream — which is what dailies reviewers care about.
    """
    if shot is None:
        return None, None
    hip_path = _resolve_current_hip_path()
    if hip_path is None:
        return None, None
    department = _department_from_hip_path(hip_path)
    if department is None:
        return None, None
    stream = houdini_department_stream(shot, department, owner=shot_owner_for(shot))
    if not path_matches_stream(hip_path, stream):
        return None, None
    records = list_version_records(stream)
    if not records:
        return None, None
    latest = records[0]
    if latest.version is None:
        return None, latest.title
    label = _format_version_label(latest.version)
    if hou.hipFile.hasUnsavedChanges():
        label += "*"
    return label, latest.title


def _resolve_current_hip_path() -> Path | None:
    # Mirror of HFileManager._current_hip_path; duplicated here to avoid
    # importing dcc.houdini.hipfile.filemanager (which pulls in Qt dialogs).
    hip_raw = (hou.hipFile.path() or "").strip()
    if not hip_raw:
        return None
    hip_path = Path(hou.expandString(hip_raw)).expanduser()
    if not hip_path.is_absolute():
        hip_path = (Path(hou.hscriptStringExpression("$HIP")) / hip_path).resolve()
    else:
        hip_path = hip_path.resolve()
    return hip_path


def _department_from_hip_path(hip_path: Path) -> str | None:
    # Mirror of HShotFileManager._department_from_path. Duplicated for the
    # same reason — avoid pulling the file-manager module's heavy deps.
    parent_name = hip_path.parent.name.strip().lower()
    if parent_name in DEPARTMENT_OPTIONS:
        return parent_name
    if hip_path.suffix.lower() != ".hipnc":
        return None
    stem = hip_path.stem.strip().lower()
    if ".v" in stem:
        stem = stem.rsplit(".v", 1)[0]
    if stem in DEPARTMENT_OPTIONS:
        return stem
    return None


__all__ = [
    "HUD_FONT_PATH",
    "build_hud_filter_args",
    "resolve_current_hip_version",
]
