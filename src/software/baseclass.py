"""Compatibility shim — real definitions live in `framework.launcher` / `framework.interface`.

Existing `from software.baseclass import DCC, DCCLocalizer` imports continue
to resolve here under their old names. The structural refactor's Phase 5
rewrites callers to import from `framework` directly under the new names
(`Launcher`, `DCCRuntime`); this file is deleted then.

`DCCLocalizer` is preserved as an alias for the `DCCRuntime` ABC. The
empty concrete-base form (with the unused `id` field) is gone — per-DCC
runtime classes inherit directly from the ABC.
"""

from framework.interface import DCCRuntime as DCCLocalizer
from framework.launcher import Launcher as DCC

# Re-export the interface aliases so that callers reaching
# `software.baseclass.DCCInterface` / `DCCLocalizerInterface` (transitively
# via the old layout) keep working.
from .interface import DCCInterface, DCCLocalizerInterface

__all__ = ["DCC", "DCCLocalizer", "DCCInterface", "DCCLocalizerInterface"]
