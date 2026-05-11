"""Compatibility shim — real implementation lives in `core.users`.

Existing `from shared.users import resolve_artist_display_name` imports
continue to resolve here through Phase 5 of the structural refactor.
"""

from __future__ import annotations

from core.users import resolve_artist_display_name

__all__ = ["resolve_artist_display_name"]
