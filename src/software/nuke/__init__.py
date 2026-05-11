"""Compatibility shim — real implementation lives in `dcc.nuke`."""

from __future__ import annotations

from dcc.nuke.launch import NukeLauncher as NukeDCC

__all__ = ["NukeDCC"]
