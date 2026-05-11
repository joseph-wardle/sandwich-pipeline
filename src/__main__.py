#!/usr/bin/env python3
from __future__ import annotations

import logging
import site

from argparse import ArgumentParser

from framework.dispatch import find_implementation
from framework.interface import DCCLauncher

r"""Launch the BYU 2026 Capstone pipeline ("Sandwich Kwon Do")

With much credit to Scott Milner and the 2025 Capstone team.

And additional credit to Dallin Clark and the 2026 capstone team.

When run as a script, parse the software from the command line
arguments, then run launch().
"""


# Configure logging
log = logging.getLogger(__name__)


def getLevelNamesMapping():
    """Implement the same-named method from the logging module.

    TODO: REPLACE ONCE OUR PYTHON IS >= 3.11
    """
    return logging._nameToLevel.keys()


def launch(
    software_name: str,
    is_python_shell: bool = False,
    extra_args: list[str] | None = None,
) -> None:
    # The dispatch string still points at `software.<name>` while Phase 4 of
    # the structural refactor moves per-DCC launchers to `dcc.<name>`. Phase 4
    # rewrites this to `f"dcc.{software_name}"`.
    launcher_cls = find_implementation(DCCLauncher, f"software.{software_name}")
    launcher_cls(is_python_shell, extra_args).launch()


if __name__ == "__main__":
    parser = ArgumentParser(description="Launch pipeline software")
    parser.add_argument(
        "software",
        help="launch the specified software",
    )
    parser.add_argument(
        "-l",
        "--log-level",
        help="log at the specified level. Possible values are %(choices)s (default: %(default)s)",
        choices=getLevelNamesMapping(),
        default=logging.getLevelName(logging.root.level),
        type=str.upper,
        metavar="LEVEL",
    )
    parser.add_argument(
        "-p",
        "--python",
        help="Open a Python shell in this DCC instead of launching the GUI",
        action="store_true",
    )

    args, extras = parser.parse_known_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(processName)s(%(process)s) %(threadName)s [%(name)s(%(lineno)s)] [%(levelname)s] %(message)s",
    )

    # Windows Python explicitly needs site.main to be called
    site.main()

    launch(args.software, args.python, extras)

    log.info("Exiting")
