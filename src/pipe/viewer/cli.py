"""`pipe view` entry point: run the viewer from a shell."""

from __future__ import annotations

import subprocess
import sys
from argparse import ArgumentParser
from pathlib import Path

from pipe.viewer.spawn import ViewerSpawnError, viewer_command, viewer_env


def view_main(argv: list[str]) -> int:
    parser = ArgumentParser(
        prog="pipe view",
        description="Open the playblast viewer on a preview spec",
    )
    parser.add_argument(
        "spec",
        type=Path,
        help="preview spec JSON written by the DCC after rendering",
    )
    args = parser.parse_args(argv)

    try:
        command = viewer_command(args.spec)
    except ViewerSpawnError as exc:
        print(f"pipe view: {exc}", file=sys.stderr)
        return 1

    # Foreground, unlike `spawn_viewer`: from a shell the viewer should act
    # like a normal CLI program — Ctrl-C works, exit code propagates.
    return subprocess.call(command, env=viewer_env())


__all__ = ["view_main"]
