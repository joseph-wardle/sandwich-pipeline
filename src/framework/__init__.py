"""DCC integration framework — abstract contracts, launcher base, dispatch.

The framework defines the surface every per-DCC integration plugs into.
Concrete implementations live in `dcc.<name>` and are discovered by
`find_implementation` at launch time.
"""

from .dispatch import find_implementation
from .interface import DCCLauncher, DCCRuntime
from .launcher import Launcher

__all__ = [
    "DCCLauncher",
    "DCCRuntime",
    "Launcher",
    "find_implementation",
]
