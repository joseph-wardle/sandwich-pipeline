"""Each attempt at a task, read from its Tractor command log.

The log holds every attempt, oldest first. Each starts with a header naming the
command, the attempt and the blade, and ends with the exit code, such as:

    ====[2026/11/18 14:52:58 /J18230/T71/C68.3/pdolbin@cs-1017451 on yoshi ]====
    ...the command's output...
    ====[2026/11/18 14:53:56 process complete, exit code: 137]====

An attempt with no exit line was cut off: the blade stopped reporting, or
Tractor gave the command to another blade, before it finished.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

_HEADER = re.compile(
    r"^====\[(?P<when>\d{4}/\d\d/\d\d \d\d:\d\d:\d\d) "
    r"/J(?P<jid>\d+)/T(?P<tid>\d+)/C(?P<cid>\d+)\.(?P<number>\d+)/"
    r"(?P<sender>\S+) on (?P<blade>\S+) \]====[ \t]*$",
    re.MULTILINE,
)
_EXIT = re.compile(
    r"^====\[(?P<when>\d{4}/\d\d/\d\d \d\d:\d\d:\d\d) process complete, "
    r"exit code: (?P<code>-?\d+)\]====[ \t]*$",
    re.MULTILINE,
)
_TIME_FORMAT = "%Y/%m/%d %H:%M:%S"


@dataclass(frozen=True)
class Attempt:
    jid: int
    tid: int
    cid: int
    # Tractor counts each command's attempts from 1.
    number: int
    blade: str
    # Seconds since the epoch. The engine logs in the farm's local time.
    started: float
    ended: float | None
    # None when the log stops before the command did.
    exit_code: int | None
    # The command's output, headers aside.
    text: str

    @property
    def key(self) -> str:
        """Names this attempt uniquely on the engine, as its header does."""
        return f"J{self.jid}/T{self.tid}/C{self.cid}.{self.number}"


def parse(log_text: str) -> list[Attempt]:
    """Every attempt the log records, oldest first."""
    headers = list(_HEADER.finditer(log_text))
    found = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(log_text)
        body = log_text[header.end() : end]
        # The last exit line, as a command's own output never prints one.
        exit_line = None
        for exit_line in _EXIT.finditer(body):
            pass
        found.append(
            Attempt(
                jid=int(header["jid"]),
                tid=int(header["tid"]),
                cid=int(header["cid"]),
                number=int(header["number"]),
                blade=header["blade"],
                started=_seconds(header["when"]),
                ended=_seconds(exit_line["when"]) if exit_line else None,
                exit_code=int(exit_line["code"]) if exit_line else None,
                text=body[: exit_line.start()] if exit_line else body,
            )
        )
    return found


def _seconds(text: str) -> float:
    return datetime.strptime(text, _TIME_FORMAT).timestamp()
