"""Sandwich pipeline — single owned top-level package.

All first-party pipeline code lives under `pipe` (`pipe.core`, `pipe.dcc`,
`pipe.framework`) so the pipeline occupies exactly one top-level import name on
every interpreter's `sys.path`. This avoids bare top-level names (notably
`core`) colliding with modules that DCCs build into their embedded Python — see
context/adr/0005.
"""

from __future__ import annotations
