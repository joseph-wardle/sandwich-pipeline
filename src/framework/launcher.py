from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import typing

from core import telemetry
from core.util.util import fix_launcher_metadata

from .interface import DCCLauncher

"""Concrete launcher base — shared subprocess + env + telemetry machinery."""

log = logging.getLogger(__name__)

# DCC launch failures are tagged on the telemetry event but never raised as a
# typed exception — the original exception (e.g. FileNotFoundError) is allowed
# to propagate so the caller sees the real cause, and `telemetry_event.fail()`
# carries the classification on the side.
_DCC_LAUNCH_FAILED_CODE = "DCC_LAUNCH_FAILED"


class Launcher(DCCLauncher):
    """Concrete launcher base.

    Provides shared subprocess launch machinery: env-var snapshot, telemetry
    instrumentation, and pre-launch hooks. Per-DCC launchers inherit from
    this and override `__init__` to construct the appropriate command, args,
    and env vars; the inherited `launch()` does the rest.
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

    @staticmethod
    def _command_basename(command: str) -> str:
        normalized = str(command).strip().replace("\\", "/").rstrip("/")
        if not normalized:
            return "<empty>"
        return normalized.split("/")[-1] or "<empty>"

    def _launch_payload(
        self, command: str, args: typing.Sequence[str] | None
    ) -> dict[str, object]:
        return {
            "command_basename": self._command_basename(command),
            "arg_count": len(list(args or [])),
            "env_keys_set": sorted({str(key) for key in self.env_vars.keys()}),
        }

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

        with telemetry.record(
            telemetry.EVENT_DCC_LAUNCH,
            payload=self._launch_payload(command, args),
        ) as telemetry_event:
            try:
                fix_launcher_metadata()
                pre_launch_tasks()
                venv = self._get_env_vars()

                log.info("Launching the software")
                log.debug(f"Command: {command}, Args: {args}")
                return_code = subprocess.call([command] + list(args or []), env=venv)
            except Exception as exc:
                # Tag the failure on the dashboard, then let the original
                # exception type propagate to the caller unchanged.
                telemetry_event.fail(
                    _DCC_LAUNCH_FAILED_CODE, str(exc) or exc.__class__.__name__
                )
                raise

            if return_code != 0:
                # Non-zero DCC exit is recorded as an error event, but is
                # not raised — DCCs exit non-zero for many recoverable
                # reasons (artist closed without saving, etc.).
                telemetry_event.fail(
                    _DCC_LAUNCH_FAILED_CODE,
                    f"DCC exited with return code {return_code}",
                )
                log.warning("DCC launch returned non-zero exit code: %s", return_code)
