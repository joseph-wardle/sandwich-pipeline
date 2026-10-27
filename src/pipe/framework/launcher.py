from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import TYPE_CHECKING

from pipe.core.util.branding import ASCII_LOGO
from pipe.core.util.paths import fix_launcher_metadata

if TYPE_CHECKING:
    import typing


from .interface import DCCLauncher

"""Concrete launcher base — shared subprocess + env machinery."""

log = logging.getLogger(__name__)


class Launcher(DCCLauncher):
    """
    Provides shared subprocess launch machinery:
        - env-var snapshot
        - and pre-launch hooks.
    Per-DCC launchers inherit from this and override `__init__`
    to construct the appropriate command, args, and env vars
    """

    command: str
    args: list[str] | None
    env_vars: typing.Mapping[str, int | str | None]
    pre_launch_tasks: typing.Callable[[], None]

    def __init__(
        self,
        command: str,
        args: typing.Sequence[str] | None = None,
        env_vars: typing.Mapping[str, int | str | None] | None = None,
        pre_launch_tasks: typing.Callable[[], None] | None = None,
    ) -> None:
        """Initialize the launcher.

        Keyword arguments:
        - command           -- the command to launch the DCC
        - args              -- args to pass to the command
        - env_vars          -- env vars to set/unset before launching
        - pre_launch_tasks  -- callable to run just before subprocess.call
        """

        if args is None:
            args = []

        self.command = command
        self.args = list(args) if args else None
        self.env_vars = env_vars or {}
        self.pre_launch_tasks = pre_launch_tasks or (lambda: None)

    def _get_env_vars(
        self, env_vars: typing.Mapping[str, int | str | None] | None = None
    ) -> dict[str, str]:
        """(Un)Set environment variables to their associated values.

        All values will be converted to strings. If a value is None,
        that environment variable will be unset.
        """
        BASE_ENVIRON = "BASE_ENVIRON"

        if BASE_ENVIRON not in os.environ:
            venv = os.environ.copy()
            venv[BASE_ENVIRON] = json.dumps(venv)
        else:
            venv = json.loads(os.environ[BASE_ENVIRON])

        if env_vars is None:
            env_vars = self.env_vars

        log.info("(Un)setting environment vars")

        for key, val in env_vars.items():
            if val is None:
                if key in venv:
                    del venv[key]
            else:
                venv[key] = str(val)

        PYTHONPATH = "PYTHONPATH"
        if PYTHONPATH not in venv:
            venv[PYTHONPATH] = ""

        log.debug(f"PYTHONPATH for launch: {venv[PYTHONPATH]}")
        return venv

    def launch(
        self,
        command: str | None = None,
        args: typing.Sequence[str] | None = None,
        pre_launch_tasks: typing.Callable[[], None] | None = None,
    ) -> None:
        """Launch the DCC subprocess.

        Passing in optional parameters overrides their default values
        from `__init__`.
        """

        if command is None:
            command = self.command
        if args is None:
            args = self.args
        if pre_launch_tasks is None:
            pre_launch_tasks = self.pre_launch_tasks

        fix_launcher_metadata()
        pre_launch_tasks()
        venv = self._get_env_vars()
        print(ASCII_LOGO)
        log.info("Launching the software")
        log.debug(f"Command: {command}, Args: {args}")
        return_code = subprocess.call([command] + list(args or []), env=venv)

        # Not raised: DCCs exit non-zero for many recoverable reasons
        # (artist closed without saving, etc.).
        if return_code != 0:
            log.warning("DCC launch returned non-zero exit code: %s", return_code)
