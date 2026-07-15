"""File naming for a previs sequence.

Pure string logic: derive the bare sequence letter, canonicalize a label, build
the `<letter>_<label>_v###.mb` workspace filename that both the manifest's
FileRecord and the Maya file ops use, and the `<code>_v###.mov` take filename that
export writes.
"""

from __future__ import annotations

import re

from pipe.core.shotgrid.paths import is_previs_shot_code

from .codes import normalize_code

WORKSPACE_SUFFIX = ".mb"
TAKE_SUFFIX = ".mov"
VERSION_PAD = 3
_VERSION_PREFIX = "v"

# A label is slugified rather than rejected: any run of non-alphanumeric
# characters (spaces, separators, punctuation) collapses to a single underscore.
# See normalize_label.
_LABEL_SEP_RE = re.compile(r"[^a-z0-9]+")


def sequence_letter(sequence_code: str) -> str:
    """The bare sequence letter (``A``) for a previs sequence code (``A_previs``).

    Raises ``ValueError`` if ``sequence_code`` is not a previs sequence code, so
    a workspace filename can never be built from a non-previs location.
    """
    if not is_previs_shot_code(sequence_code):
        raise ValueError(
            f"{sequence_code!r} is not a previs sequence code "
            "(expected <LETTER>_previs, e.g. 'A_previs')"
        )
    return sequence_code[0]


def normalize_label(label: str) -> str:
    """Slugify a label to its canonical form, or raise ValueError if nothing usable.

    Case is folded and any run of non-alphanumeric characters collapses to a single
    underscore, so "Layout Pass", "layout-pass", and "layout_pass" all name the one
    stream `layout_pass` — the same collapse codes.normalize_code applies to shot
    codes. Non-ASCII letters count as separators (`café` → `caf`). Only input with no
    letters or digits at all (blank or pure punctuation) is rejected.
    """
    canonical = _LABEL_SEP_RE.sub("_", label.strip().lower()).strip("_")
    if not canonical:
        raise ValueError(
            f"{label!r} has no letters or digits to build a file label from "
            "(try something like 'blocking' or 'layout pass')."
        )
    return canonical


def version_token(version: int) -> str:
    """The zero-padded ``v###`` token for a version number (``3`` → ``'v003'``)."""
    return f"{_VERSION_PREFIX}{version:0{VERSION_PAD}d}"


def workspace_filename(sequence_code: str, label: str, version: int) -> str:
    """Build `<letter>_<label>_v###.mb`.

    label must already be canonical (see normalize_label); the sole caller
    normalizes once so the manifest's stored label matches the filename.
    """
    letter = sequence_letter(sequence_code)
    return f"{letter}_{label}_{version_token(version)}{WORKSPACE_SUFFIX}"


def take_filename(code: str, version: int) -> str:
    """Build a take's playblast filename `<code>_v###.mov` (e.g. `A_010_v003.mov`).

    The code is canonicalized (see codes.normalize_code), so a malformed code raises
    ValueError before it can name a file on disk — the guard workspace_filename
    applies via sequence_letter.
    """
    canonical = normalize_code(code)
    return f"{canonical}_{version_token(version)}{TAKE_SUFFIX}"


__all__ = [
    "WORKSPACE_SUFFIX",
    "TAKE_SUFFIX",
    "VERSION_PAD",
    "sequence_letter",
    "normalize_label",
    "version_token",
    "workspace_filename",
    "take_filename",
]
