"""Where the doctor keeps its files, and what it writes down as it works.

Its login lives in ~/.config/sandwich/farm-doctor, readable only by its user.
What it did and what it saw live in ~/.local/state/sandwich/farm-doctor, apart
from the login, so they can be read or shared without it:

- journal.jsonl: every retry it made, or would have made while watching only.
- status.json: whether it is running, where, and how its last round went.
- history.jsonl: the failed attempts it has seen, for `blades.History`.
- cleared.json: machines given a fresh start, as after a reboot: their
  failures before then no longer count, and retries stop avoiding them.
- focus.json: machines in hyperfocus, to test them: retries go to them first,
  while they're free. The window and `doctor focus` write it.
- pins.json: the tasks the doctor sent to a machine in hyperfocus, so it can
  free them should the machine go before they start. Only the doctor writes
  it, so the window's writes to focus.json never race the doctor's.

Home folders are shared by every lab computer, so the Sandwich Renderer window
reads these wherever the doctor runs.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# How many journal lines the window reads back.
JOURNAL_LINES = 300
# How long a fresh start is remembered: by then its machine's earlier
# failures have long left the doctor's history.
_CLEARED_SECONDS = 7 * 24 * 3600
# How long hyperfocus lasts unless stopped: long enough for a few test tasks,
# short enough that a forgotten one doesn't keep sending work to a bad machine.
FOCUS_SECONDS = 4 * 3600


@dataclass(frozen=True)
class Paths:
    config: Path
    state: Path

    @property
    def password(self) -> Path:
        return self.config / "password"

    @property
    def session(self) -> Path:
        """This computer's Tractor session: the engine ties a session to the
        computer that logged in, so each keeps its own."""
        return self.config / f"tractor.{socket.gethostname()}.session"

    @property
    def refused(self) -> Path:
        """Notes the password file Tractor last refused, so it isn't sent again."""
        return self.config / "refused"

    @property
    def journal(self) -> Path:
        return self.state / "journal.jsonl"

    @property
    def status(self) -> Path:
        return self.state / "status.json"

    @property
    def history(self) -> Path:
        return self.state / "history.jsonl"

    @property
    def cleared(self) -> Path:
        return self.state / "cleared.json"

    @property
    def focus(self) -> Path:
        return self.state / "focus.json"

    @property
    def pins(self) -> Path:
        return self.state / "pins.json"

    @property
    def lock(self) -> Path:
        """Held by the running doctor, so only one acts at a time."""
        return self.state / "doctor.lock"


def default_paths() -> Paths:
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    state = Path(os.environ.get("XDG_STATE_HOME") or home / ".local" / "state")
    return Paths(
        config / "sandwich" / "farm-doctor", state / "sandwich" / "farm-doctor"
    )


def write_journal(paths: Paths, entry: dict[str, Any]) -> None:
    paths.state.mkdir(parents=True, exist_ok=True)
    with paths.journal.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry) + "\n")


def read_journal(paths: Paths, lines: int = JOURNAL_LINES) -> list[dict[str, Any]]:
    """The journal's last `lines` entries, oldest first."""
    try:
        text = paths.journal.read_text(encoding="utf-8")
    except OSError:
        return []
    entries = []
    for line in text.splitlines()[-lines:]:
        try:
            entries.append(json.loads(line))
        except ValueError:
            log.warning(
                "Skipped an unreadable line of the doctor's journal: %r", line[:200]
            )
    return entries


def write_status(paths: Paths, status: dict[str, Any]) -> None:
    paths.state.mkdir(parents=True, exist_ok=True)
    temporary = paths.status.with_suffix(".tmp")
    temporary.write_text(json.dumps(status, indent=1), encoding="utf-8")
    # Replaced whole, so the window never reads half of it.
    temporary.replace(paths.status)


def read_status(paths: Paths) -> dict[str, Any] | None:
    try:
        return json.loads(paths.status.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_cleared(paths: Paths) -> dict[str, dict[str, Any]]:
    """The machines given a fresh start, each with "when" and "by"."""
    try:
        found = json.loads(paths.cleared.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(found, dict):
        return {}
    return {
        machine: entry
        for machine, entry in found.items()
        if isinstance(entry, dict) and isinstance(entry.get("when"), int | float)
    }


def cleared_since(paths: Paths) -> dict[str, float]:
    """When each machine was given a fresh start."""
    return {machine: entry["when"] for machine, entry in read_cleared(paths).items()}


def clear(paths: Paths, machine: str, by: str, undo: bool = False) -> None:
    """Give `machine` a fresh start, noted as given `by` someone, or with
    `undo`, take it back."""
    now = time.time()
    cleared = {
        name: entry
        for name, entry in read_cleared(paths).items()
        if now - entry["when"] < _CLEARED_SECONDS and name != machine
    }
    if not undo:
        cleared[machine] = {"when": now, "by": by}
    paths.state.mkdir(parents=True, exist_ok=True)
    temporary = paths.cleared.with_suffix(".tmp")
    temporary.write_text(json.dumps(cleared, indent=1), encoding="utf-8")
    temporary.replace(paths.cleared)


def read_focus(paths: Paths) -> dict[str, dict[str, Any]]:
    """The machines in hyperfocus now, each with "since", "until" and "by"."""
    now = time.time()
    return {
        machine: entry
        for machine, entry in _read_json(paths.focus).items()
        if isinstance(entry, dict)
        and isinstance(entry.get("until"), int | float)
        and entry["until"] > now
    }


def focus(paths: Paths, machine: str, by: str, stop: bool = False) -> None:
    """Put `machine` in hyperfocus, noted as `by` someone, or with `stop`,
    take it out."""
    focused = read_focus(paths)
    focused.pop(machine, None)
    if not stop:
        now = time.time()
        focused[machine] = {"since": now, "until": now + FOCUS_SECONDS, "by": by}
    _write_json(paths, paths.focus, focused)


def read_pins(paths: Paths) -> list[dict[str, Any]]:
    """The tasks the doctor sent to machines in hyperfocus: each with "jid",
    "tid", "machine" and "when"."""
    found = _read_json(paths.pins).get("pins")
    return [pin for pin in found or [] if isinstance(pin, dict)]


def write_pins(paths: Paths, pins: list[dict[str, Any]]) -> None:
    _write_json(paths, paths.pins, {"pins": pins})


def _read_json(path: Path) -> dict[str, Any]:
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _write_json(paths: Paths, path: Path, data: dict[str, Any]) -> None:
    paths.state.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=1), encoding="utf-8")
    # Replaced whole, so no reader sees half of it.
    temporary.replace(path)
