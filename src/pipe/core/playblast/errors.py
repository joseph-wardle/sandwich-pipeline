from __future__ import annotations

import errno

from pipe.core.playblast.encoding import FFmpegEncodeError

_LOG_HINT = "Check the script editor for the full error."

# Past this, an exception's message is tool output rather than a sentence.
_MAX_EXCERPT = 120


def artist_reason(exc: Exception) -> str:
    """A brief, actionable sentence for `exc`, safe to put in a QLabel."""
    if isinstance(exc, PermissionError):
        return (
            f"No permission to write to {_target(exc)}. "
            "Ask a TD to check that folder, then try again."
        )
    if isinstance(exc, FileNotFoundError):
        return (
            f"Could not find {_target(exc)}. The preview's temp files may have "
            "been cleaned up. Close the viewer and playblast again."
        )
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return (
            f"The disk holding {_target(exc)} is full. Free some space, then "
            "try again."
        )
    if isinstance(exc, FFmpegEncodeError):
        # Its message carries ffmpeg's stderr, which belongs in the log.
        return f"The movie could not be encoded. {_LOG_HINT}"
    return f"{_excerpt(exc)} {_LOG_HINT}"


def _target(exc: Exception) -> str:
    filename = exc.filename if isinstance(exc, OSError) else None
    return str(filename) if filename else "the destination folder"


def _excerpt(exc: Exception) -> str:
    """The exception's own first line, when it is short enough to read."""
    lines = str(exc).strip().splitlines()
    if lines and len(lines[0]) <= _MAX_EXCERPT:
        return lines[0] if lines[0].endswith((".", "!", "?")) else f"{lines[0]}."
    return "Something went wrong."


__all__ = ["artist_reason"]
