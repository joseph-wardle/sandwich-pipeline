"""Compatibility shim — real definitions live in `core.util.util`.

Existing `from shared.util import X` imports continue to resolve here through
Phase 5 of the structural refactor, which rewrites callers to import from
`core.util.util` (path/launcher helpers) or `framework.dispatch`
(`find_implementation`) directly and deletes this shim.
"""

from __future__ import annotations

from core.util.util import (
    fix_launcher_metadata,
    get_anim_path,
    get_asset_path,
    get_character_path,
    get_documentation_path,
    get_edit_path,
    get_function_source_code_url,
    get_groups_path,
    get_lib_path,
    get_pipe_path,
    get_previs_path,
    get_production_path,
    get_repo_root,
    get_rig_build_path,
    get_rigging_path,
    get_shared_telemetry_backend_dir,
    get_shared_telemetry_spool_dir,
    resolve_mapped_path,
)
from framework.dispatch import find_implementation

__all__ = [
    "find_implementation",
    "fix_launcher_metadata",
    "get_anim_path",
    "get_asset_path",
    "get_character_path",
    "get_documentation_path",
    "get_edit_path",
    "get_function_source_code_url",
    "get_groups_path",
    "get_lib_path",
    "get_pipe_path",
    "get_previs_path",
    "get_production_path",
    "get_repo_root",
    "get_rig_build_path",
    "get_rigging_path",
    "get_shared_telemetry_backend_dir",
    "get_shared_telemetry_spool_dir",
    "resolve_mapped_path",
]
