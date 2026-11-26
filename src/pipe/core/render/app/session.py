"""A shot's render hip, kept open in a headless Houdini the window talks to."""

from __future__ import annotations

import logging
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

from Qt import QtCore, QtNetwork

from pipe.core.render.protocol import Reply, request_line
from pipe.core.util.paths import get_repo_root

_REPO = get_repo_root()
_VENV_PYTHON = _REPO / ".venv" / "bin" / "python"
_WORKER = _REPO / "src" / "pipe" / "dcc" / "houdini" / "render_network" / "worker.py"
# mayapy sets these for its own libraries, and Houdini would load Maya's Qt.
_MAYA_ONLY = ("LD_LIBRARY_PATH", "MAYA_LOCATION", "PYTHONHOME", "QT_PLUGIN_PATH")
# The launcher's banner, which says nothing in a log.
_BANNER_CHARACTERS = set("▄▀█ ")
_LOG_TAIL = 200

STOPPED = (
    "Houdini stopped, so edits that weren't saved are lost. The log below says "
    "what it was doing. Reload to open the shot again, and send the log to a TD "
    "if it happens again."
)

log = logging.getLogger(__name__)


class HoudiniSession(QtCore.QObject):
    """One hython with `shot`'s render hip open, answering requests in order.

    `output` carries Houdini's log lines, and `stopped` why it exited, if not by `stop()`.
    """

    output = QtCore.Signal(str)
    stopped = QtCore.Signal(str, str)

    _process: QtCore.QProcess
    _server: QtNetwork.QLocalServer
    _socket: QtNetwork.QLocalSocket | None
    # Requests made before Houdini connects.
    _unsent: list[bytes]
    _waiting: deque[Callable[[Reply], None]]
    _received: bytes
    _tail: list[str]
    _stopping: bool

    def __init__(self, shot: str, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._socket, self._unsent, self._waiting = None, [], deque()
        self._received, self._tail, self._stopping = b"", [], False

        self._process = QtCore.QProcess(self)
        self._process.setProgram(str(_VENV_PYTHON))
        self._process.setWorkingDirectory(str(_REPO))
        self._process.setProcessEnvironment(_environment())
        self._process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._read_output)
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._error)

        self._server = QtNetwork.QLocalServer(self)
        self._server.newConnection.connect(self._connect)
        if not self._server.listen(f"rancher_{uuid.uuid4().hex}"):
            # Later, so the window has connected to `stopped`.
            QtCore.QTimer.singleShot(0, self._not_listening)
            return
        self._process.setArguments(
            [
                *("-m", "pipe", "-l", "WARNING", "houdini", "-p", str(_WORKER)),
                *("--shot", shot, "--socket", self._server.fullServerName()),
            ]
        )
        log.info("Running %s %s", _VENV_PYTHON, " ".join(self._process.arguments()))
        self._process.start()

    def request(self, command: str, then: Callable[[Reply], None], **args: Any) -> None:
        self._waiting.append(then)
        line = request_line(command, args)
        if self._socket is None:
            self._unsent.append(line)
        else:
            self._socket.write(line)

    def stop(self) -> None:
        self._stopping = True
        # Houdini exits when the socket closes, even if killing the launcher misses it.
        if self._socket is not None:
            self._socket.abort()
        self._server.close()
        self._process.kill()
        self._process.waitForFinished(1000)

    def _not_listening(self) -> None:
        self.stopped.emit(
            "Could not open a connection for Houdini to answer on. Ask a TD to "
            "check the temp folder.",
            self._server.errorString(),
        )

    def _connect(self) -> None:
        socket = self._server.nextPendingConnection()
        socket.readyRead.connect(self._read_replies)
        for line in self._unsent:
            socket.write(line)
        self._socket, self._unsent = socket, []

    def _read_replies(self) -> None:
        assert self._socket is not None
        self._received += bytes(self._socket.readAll().data())
        # A reply's handler may stop the session.
        while b"\n" in self._received and not self._stopping:
            line, self._received = self._received.split(b"\n", 1)
            self._waiting.popleft()(Reply.from_line(line))

    def _read_output(self) -> None:
        text = bytes(self._process.readAllStandardOutput().data()).decode(
            errors="replace"
        )
        for line in text.splitlines():
            if not line.strip() or set(line) <= _BANNER_CHARACTERS:
                continue
            self._tail = [*self._tail[-_LOG_TAIL:], line]
            self.output.emit(line)

    def _error(self, error: QtCore.QProcess.ProcessError) -> None:
        if error == QtCore.QProcess.FailedToStart and not self._stopping:
            self.stopped.emit(
                f"Could not start Houdini: {_VENV_PYTHON} did not run. Ask a TD to "
                "check that the pipeline's venv is installed.",
                self._process.errorString(),
            )

    def _finished(self, *_: object) -> None:
        if not self._stopping:
            self.stopped.emit(STOPPED, "\n".join(self._tail))


def _environment() -> QtCore.QProcessEnvironment:
    env = QtCore.QProcessEnvironment.systemEnvironment()
    for name in _MAYA_ONLY:
        env.remove(name)
    env.insert("PYTHONPATH", str(_REPO / "src"))
    return env
