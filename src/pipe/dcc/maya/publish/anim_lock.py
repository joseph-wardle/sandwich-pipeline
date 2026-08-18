from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from Qt import QtWidgets

from pipe.core.ui import MessageDialog
from pipe.core.util.paths import get_production_path

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

LOCK_FILE = get_production_path() / "json" / "locks.json"
LOCKED_SEQUENCES_KEY = "anim_locked_sequences"

REPUBLISH_PHRASE = "steveisreallycool"


def is_shot_locked(
    sequence_code: str | None,
    shot_code: str | None,
    lock_file: Path = LOCK_FILE,
) -> bool:
    """Whether production has locked this shot's sequence against animation."""
    locked = _locked_sequences(lock_file)
    if locked is None:
        return True

    if sequence_code and sequence_code.strip().upper() in locked:
        return True

    shot = shot_code.strip().upper() if shot_code else ""
    return any(shot.startswith(sequence) for sequence in locked)


def confirm_locked_republish(
    parent: QtWidgets.QWidget | None,
    sequence_code: str | None,
    shot_code: str | None,
    lock_file: Path = LOCK_FILE,
) -> bool:
    """Whether animation may be published into this shot's sequence at all.

    A locked sequence refuses every animation publish, whatever the artist would
    have gone on to select — see ADR-0014. Opens a dialog when it is locked.
    """
    if not is_shot_locked(sequence_code, shot_code, lock_file=lock_file):
        return True

    name = sequence_code or shot_code or "this shot"
    phrase, entered = QtWidgets.QInputDialog.getText(
        parent,
        "Animation Locked",
        f"Animation is locked for {name}, so CFX and lighting are building on "
        "what it holds now.\n\nEnter the republish phrase to publish anyway, or "
        "cancel and talk to your lead.",
    )
    if not entered:
        return False

    if phrase.strip().lower() != REPUBLISH_PHRASE:
        MessageDialog(
            parent,
            "That is not the republish phrase, so nothing was published. Your "
            "lead can give you the phrase.",
            "Animation Locked",
        ).exec_()
        return False

    return True


def _locked_sequences(lock_file: Path) -> set[str] | None:
    """The locked sequence codes, or None if the file is there but unreadable."""
    try:
        data = json.loads(lock_file.read_text())
    except FileNotFoundError:
        log.info("No animation lock file at '%s'; no sequences are locked", lock_file)
        return set()
    except (OSError, json.JSONDecodeError):
        log.warning("Could not read '%s'", lock_file, exc_info=True)
        return None

    sequences = data.get(LOCKED_SEQUENCES_KEY, []) if isinstance(data, dict) else None
    if not isinstance(sequences, list):
        log.warning(
            "'%s' must hold a JSON object with a list under '%s'",
            lock_file,
            LOCKED_SEQUENCES_KEY,
        )
        return None

    return {str(code).strip().upper() for code in sequences if str(code).strip()}
