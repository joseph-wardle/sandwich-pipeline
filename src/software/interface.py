"""Compatibility shim — real definitions live in `framework.interface`.

Existing `from software.interface import DCCInterface, DCCLocalizerInterface`
imports continue to resolve here under their old names. The structural
refactor's Phase 5 rewrites callers to import from `framework.interface`
directly using the new names (`DCCLauncher`, `DCCRuntime`); this file is
deleted then.
"""

from framework.interface import DCCLauncher as DCCInterface
from framework.interface import DCCRuntime as DCCLocalizerInterface

__all__ = ["DCCInterface", "DCCLocalizerInterface"]
