"""Keep the headless doctor running on one computer, with cron.

Cron starts it every ten minutes; while one is running, the start finds the
doctor's lock taken and quietly exits. So the doctor comes back by itself
after the computer reboots, and logging out, which kills everything a lab
login started, never touches it, as cron starts it outside any login.

The crontab belongs to the computer it's installed on, not the home folder,
so `install` and `remove` act on the computer they run on.

Cron runs the checkout `schedule` was run from. Run it from a git worktree
held at one commit, not from a checkout you switch branches in:

    git worktree add --detach ~/sandwich-farm-doctor farm-doctor

To run newer code, move that worktree to it and stop the running doctor;
cron starts the new code within ten minutes:

    git -C ~/sandwich-farm-doctor checkout --detach farm-doctor
    kill <pid>    # on the computer `doctor status` names
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

from pipe.core.render.doctor.records import Paths
from pipe.core.tractor import FarmError

# Ends the doctor's crontab line, so it can be found and replaced.
_MARK = "# sandwich farm doctor"
_EVERY_TEN_MINUTES = "*/10 * * * *"


def install(paths: Paths, run_arguments: list[str]) -> str:
    """Have cron start `doctor run` with `run_arguments` on this computer,
    replacing any earlier doctor line, and return the line.

    It runs this same Python on this same checkout, so it runs this branch's
    code for as long as the checkout stays on it.
    """
    src = Path(__file__).resolve().parents[4]
    log = paths.state / "doctor.log"
    paths.state.mkdir(parents=True, exist_ok=True)
    command = " ".join(
        [
            f"cd {shlex.quote(str(src.parent))} &&",
            f"PYTHONPATH={shlex.quote(str(src))}",
            shlex.quote(sys.executable),
            "-m pipe.core.render.doctor run --if-not-running",
            *(shlex.quote(argument) for argument in run_arguments),
            f">> {shlex.quote(str(log))} 2>&1",
        ]
    )
    if "%" in command:
        # Cron reads % as a newline, which would cut the command short.
        raise FarmError(f"Cron can't run a command with % in it: {command}")
    line = f"{_EVERY_TEN_MINUTES} {command} {_MARK}"
    _write([kept for kept in _read() if _MARK not in kept] + [line])
    return line


def remove() -> bool:
    """Stop cron starting the doctor on this computer; False if it wasn't."""
    lines = _read()
    kept = [line for line in lines if _MARK not in line]
    if len(kept) == len(lines):
        return False
    _write(kept)
    return True


def _read() -> list[str]:
    done = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        # Also how crontab says there is none yet.
        if "no crontab" in done.stderr:
            return []
        raise FarmError(
            f"Could not read this computer's crontab: {done.stderr.strip()}"
        )
    return done.stdout.splitlines()


def _write(lines: list[str]) -> None:
    text = "".join(line + "\n" for line in lines)
    done = subprocess.run(
        ["crontab", "-"], input=text, capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise FarmError(
            f"Could not change this computer's crontab: {done.stderr.strip()}"
        )
