"""Compatibility shim package — pipeline domain modules now live under `core.*`
and per-DCC integration packages live under `dcc.*`.

Phase 3 of the structural refactor moved nine cross-DCC domain packages plus
the shared `util/` package and `texconverter.py` out of `pipe/` and into
`core/`. Phase 4 moved the per-DCC subpackages (`pipe.maya`, `pipe.houdini`,
`pipe.blender`, `pipe.substance_painter`) into `dcc.<name>` and migrated the
DCC-context gating into `dcc/__init__.py`.

Each `pipe/<X>[/<sub>].py` shim under this package re-binds the corresponding
canonical `core.*` or `dcc.*` module via `sys.modules` so identity checks
(`isinstance`, `is`) hold across the legacy `pipe.*` and canonical paths.
Phase 5 of the refactor rewrites every caller to import from the canonical
paths directly and deletes the shims.
"""

from __future__ import annotations

import logging as _logging
from os import environ as _environ

_logging.basicConfig(
    level=int(_environ.get("PIPE_LOG_LEVEL") or 0),
    format="%(asctime)s %(processName)s(%(process)s) %(threadName)s [%(name)s(%(lineno)s)] [%(levelname)s] %(message)s",
)
