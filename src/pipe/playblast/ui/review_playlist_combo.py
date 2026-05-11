"""Compatibility shim — real implementation lives in `core.playblast.ui.review_playlist_combo`."""

from __future__ import annotations

import sys as _sys

import core.playblast.ui.review_playlist_combo as _real

# Re-bind the legacy `pipe.playblast.ui.review_playlist_combo` name onto the canonical `core.playblast.ui.review_playlist_combo`
# module object. After this assignment, `sys.modules["pipe.playblast.ui.review_playlist_combo"]` and
# `sys.modules["core.playblast.ui.review_playlist_combo"]` point at the same module, so subpath
# identity (`from pipe.playblast.ui.review_playlist_combo import X is core.playblast.ui.review_playlist_combo.X`) holds.
_sys.modules[__name__] = _real

from core.playblast.ui.review_playlist_combo import *  # noqa: E402, F401, F403
