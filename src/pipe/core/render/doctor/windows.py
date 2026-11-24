"""Which lab machines are running Windows right now.

A lab machine runs Windows as a virtual machine, started from its Linux login
screen (/usr/local/bin/windows_button.py runs `virsh start win11`). The
machine stays on Linux underneath, nimby'd so the farm leaves it alone, and
the Windows inside gets its own network name, <machine>-win.cs.byu.edu. A
running Windows answers on its RPC port; a stopped one doesn't answer at all.

Only nimby'd machines need asking: one taking farm work isn't running
Windows. Asking needs no login, and nothing here can see who is logged in to
Windows or stop it, which takes the lab's own rights on each machine.
"""

from __future__ import annotations

import socket
from concurrent.futures import ThreadPoolExecutor

from pipe.core.tractor import Blade

_DOMAIN = "cs.byu.edu"
# Windows's RPC endpoint mapper, which answers whenever Windows is up.
_RPC_PORT = 135
# A running Windows answers in milliseconds; one that's off never does.
_TIMEOUT_SECONDS = 1.5
_ASKED_AT_ONCE = 32


def running_windows(blades: list[Blade]) -> set[str]:
    """The names of the blades whose Windows is running, asking only those
    nimby'd, all at once."""
    nimbied = [blade.name for blade in blades if blade.nimby]
    if not nimbied:
        return set()
    with ThreadPoolExecutor(min(_ASKED_AT_ONCE, len(nimbied))) as pool:
        answered = pool.map(_answers, nimbied)
    return {name for name, up in zip(nimbied, answered) if up}


def _answers(machine: str) -> bool:
    """Whether the machine's Windows answers on its RPC port."""
    host = f"{machine.split('.')[0]}-win.{_DOMAIN}"
    try:
        with socket.create_connection((host, _RPC_PORT), timeout=_TIMEOUT_SECONDS):
            return True
    except OSError:
        # No such name, as for machines without Windows, or no answer.
        return False
