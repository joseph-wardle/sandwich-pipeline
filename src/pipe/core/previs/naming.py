"""File naming for a previs sequence: `<letter>_<label>_v###.mb` for a workspace file."""

from __future__ import annotations

import re

from pipe.core.shotgrid.paths import is_previs_shot_code

WORKSPACE_SUFFIX = ".mb"
VERSION_PAD = 3
_VERSION_PREFIX = "v"

# A label is normalized by replacing any run of non-alphanumeric
# characters (spaces, separators, punctuation) with an underscore.
_LABEL_SEP_RE = re.compile(r"[^a-z0-9]+")


def sequence_letter(sequence_code: str) -> str:
    """The bare sequence letter (`A`) for a previs sequence code (`A_previs`)."""
    if not is_previs_shot_code(sequence_code):
        raise ValueError(
            f"{sequence_code!r} is not a previs sequence code "
            "(expected <LETTER>_previs, e.g. 'A_previs')"
        )
    return sequence_code[0]


def normalize_label(label: str) -> str:
    """Normalize a label to its canonical form, or raise ValueError if nothing usable.

    "Layout Pass", "layout-pass", and "layout_pass" all name the one stream `layout_pass`
    Only an input with no letters or digits at all is rejected.
    """
    canonical = _LABEL_SEP_RE.sub("_", label.strip().lower()).strip("_")
    if not canonical:
        raise ValueError(
            f"{label!r} has no letters or digits to build a file label from "
            "(try something like 'blocking' or 'layout pass')."
        )
    return canonical


def version_token(version: int) -> str:
    """The zero-padded `v###` token for a version number (`3` → `'v003'`)."""
    return f"{_VERSION_PREFIX}{version:0{VERSION_PAD}d}"


def workspace_filename(sequence_code: str, label: str, version: int) -> str:
    """Build `<letter>_<label>_v###.mb`."""
    letter = sequence_letter(sequence_code)
    return f"{letter}_{label}_{version_token(version)}{WORKSPACE_SUFFIX}"


__all__ = [
    "WORKSPACE_SUFFIX",
    "VERSION_PAD",
    "sequence_letter",
    "normalize_label",
    "version_token",
    "workspace_filename",
]
