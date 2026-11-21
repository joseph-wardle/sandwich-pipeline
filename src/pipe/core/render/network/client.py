"""Run the headless Houdini worker from the window, one command at a time."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from Qt import QtCore

from pipe.core.render.network.protocol import (
    Failure,
    LightingChoice,
    Result,
    Edits,
)
from pipe.core.util.paths import get_repo_root

_REPO = get_repo_root()
_VENV_PYTHON = _REPO / ".venv" / "bin" / "python"
_WORKER = _REPO / "src" / "pipe" / "dcc" / "houdini" / "render_network" / "worker.py"
# mayapy sets these for its own libraries, and Houdini would load Maya's Qt.
_MAYA_ONLY = ("LD_LIBRARY_PATH", "MAYA_LOCATION", "PYTHONHOME", "QT_PLUGIN_PATH")
# The launcher's banner, which says nothing in a log.
_BANNER_CHARACTERS = set("▄▀█ ")

STOPPED = (
    "Houdini stopped before it answered, so nothing was saved or sent. The log "
    "below says what it was doing. Try again, and send the log to a TD if it "
    "happens again."
)
CANCELLED = "Cancelled. Nothing was saved or sent unless the log says so."

log = logging.getLogger(__name__)


class WorkerRun(QtCore.QObject):
    """One worker command. `output` carries its log lines as they come, and
    `finished` its Result once it exits."""

    output = QtCore.Signal(str)
    finished = QtCore.Signal(object)

    _process: QtCore.QProcess
    _folder: Path
    _result: Path
    _shot: str
    _cancelled: bool
    _tail: list[str]

    def __init__(
        self,
        command: str,
        shot: str,
        edits: Edits | None = None,
        parent: QtCore.QObject | None = None,
        choice: LightingChoice | None = None,
    ) -> None:
        super().__init__(parent)
        self._shot = shot
        self._cancelled = False
        self._tail = []
        self._folder = Path(tempfile.mkdtemp(prefix="render_network_"))
        self._result = self._folder / "result.json"
        arguments = [
            *("-m", "pipe", "-l", "WARNING", "houdini", "-p", str(_WORKER)),
            *(command, "--shot", shot, "--result", str(self._result)),
        ]
        if edits is not None:
            edits.write(self._folder / "values.json")
            arguments += ["--values", str(self._folder / "values.json")]
        if choice is not None:
            choice.write(self._folder / "choice.json")
            arguments += ["--choice", str(self._folder / "choice.json")]

        self._process = QtCore.QProcess(self)
        self._process.setProgram(str(_VENV_PYTHON))
        self._process.setArguments(arguments)
        self._process.setWorkingDirectory(str(_REPO))
        self._process.setProcessEnvironment(_environment())
        self._process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._read)
        self._process.finished.connect(self._finish)
        self._process.errorOccurred.connect(self._error)

    def start(self) -> None:
        log.info("Running %s %s", _VENV_PYTHON, " ".join(self._process.arguments()))
        self._process.start()

    def cancel(self) -> None:
        self._cancelled = True
        self._process.kill()

    def _read(self) -> None:
        text = bytes(self._process.readAllStandardOutput().data()).decode(
            errors="replace"
        )
        for line in text.splitlines():
            if not line.strip() or set(line) <= _BANNER_CHARACTERS:
                continue
            self._tail = [*self._tail[-200:], line]
            self.output.emit(line)

    def _error(self, error: QtCore.QProcess.ProcessError) -> None:
        if error == QtCore.QProcess.FailedToStart:
            self._deliver(
                Result(
                    shot=self._shot,
                    failure=Failure(
                        f"Could not start Houdini: {_VENV_PYTHON} did not run. Ask a "
                        "TD to check that the pipeline's venv is installed.",
                        self._process.errorString(),
                    ),
                )
            )

    def _finish(self, *_: object) -> None:
        if self._cancelled:
            result = Result(shot=self._shot, failure=Failure(CANCELLED))
        elif self._result.exists():
            result = Result.read(self._result)
        else:
            result = Result(
                shot=self._shot, failure=Failure(STOPPED, "\n".join(self._tail))
            )
        self._deliver(result)

    def _deliver(self, result: Result) -> None:
        shutil.rmtree(self._folder, ignore_errors=True)
        self.finished.emit(result)


def _environment() -> QtCore.QProcessEnvironment:
    env = QtCore.QProcessEnvironment.systemEnvironment()
    for name in _MAYA_ONLY:
        env.remove(name)
    env.insert("PYTHONPATH", str(_REPO / "src"))
    return env
