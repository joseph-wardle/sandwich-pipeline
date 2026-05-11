"""Cross-DCC platform code for the sandwich pipeline.

`core` holds everything that is not tied to a specific DCC: domain models
(asset, shot, environment, versioning, ShotGrid), shared UI helpers (glui),
process telemetry, file/path utilities, and the like. Nothing under `core`
imports `maya`, `hou`, `nuke`, `substance_painter`, `pxr`, or any other
DCC-specific module — the whole subtree must import cleanly from the outer
venv.

Per-DCC implementations live under `dcc.<name>` and are free to import from
`core`. The abstract integration contracts (DCCLauncher, DCCRuntime) and the
dispatcher (find_implementation) live under `framework`.
"""

from __future__ import annotations
