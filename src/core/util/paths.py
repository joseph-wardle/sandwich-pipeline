from __future__ import annotations

import getpass
import inspect
import os
import platform
import re
import socket
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import FunctionType

from env import production_path as _prp

_DOCUMENTATION_ENV_VAR = "PIPELINE_DOCUMENTATION_URL"
_DEFAULT_DOCUMENTATION_URL = "https://github.com/joseph-wardle/sandwich-pipeline/wiki/"
_SOURCE_CODE_ROOT_URL = "https://github.com/joseph-wardle/sandwich-pipeline/tree/prod"


def fix_launcher_metadata() -> None:
    if platform.system() != "Linux":
        return
    try:
        procs = [
            subprocess.Popen(
                [
                    "gio",
                    "set",
                    str(item),
                    "metadata::caja-trusted-launcher",
                    "true",
                ]
            )
            for item in get_src_path().parent.iterdir()
            if item.suffix == ".desktop"
        ]
        for p in procs:
            p.wait()

    except Exception:
        pass


def get_anim_path() -> Path:
    return get_production_path().parent / "anim"


def get_asset_path() -> Path:
    return get_production_path() / "asset"


def get_groups_path() -> Path:
    return get_production_path() / ".."


def get_character_path() -> Path:
    return get_production_path().parent / "character"


def get_edit_path() -> Path:
    return get_production_path().parent / "edit/shots"


def get_src_path() -> Path:
    # __file__ is `src/core/util/paths.py`; parents[2] is `src/`.
    return Path(__file__).resolve().parents[2]


def get_repo_root() -> Path:
    # __file__ is `src/core/util/paths.py`; parents[3] is the repository root.
    return Path(__file__).resolve().parents[3]


def get_function_source_code_url(func: FunctionType) -> str | None:
    """
    Returns a URL pointing to the source file and lines of the given function.
    """
    try:
        filepath_string = inspect.getsourcefile(func)
        if not filepath_string:
            return None
        filepath = Path(filepath_string)
        relative_path = filepath.relative_to(get_repo_root())
        source_lines, start_line_no = inspect.getsourcelines(func)
        start_line_no = inspect.getsourcelines(func)[1]
        end_line_no = start_line_no + len(source_lines) - 1
        url = f"{_SOURCE_CODE_ROOT_URL}/{relative_path}#L{start_line_no}-L{end_line_no}"
        return url
    except Exception:
        return None


def get_documentation_path(page: str | None = None) -> str:
    """Return the documentation root or a page URL/path.

    Override the default by setting PIPELINE_DOCUMENTATION_URL to a URL or local
    path. If the override contains "{page}", it is formatted with the page
    value directly.
    """
    override = os.environ.get(_DOCUMENTATION_ENV_VAR, "").strip()
    base = override or _DEFAULT_DOCUMENTATION_URL
    if "{page}" in base:
        return base.format(page=page or "")

    root = _normalize_documentation_root(base)
    if not page:
        return root
    if "://" in root:
        return f"{root.rstrip('/')}/{page.lstrip('/')}"
    return str(Path(root) / page)


def get_previs_path() -> Path:
    return get_production_path().parent / "previs"


def get_production_path() -> Path:
    return _prp


def _sanitize_path_segment(value: str, *, fallback: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        return fallback
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized)
    sanitized = sanitized.strip("._-")
    return sanitized or fallback


def get_shared_telemetry_backend_dir() -> Path:
    """Return the shared production root for telemetry backend state.

    Holds the JSONL spool, the Postgres data directory, the Grafana data
    directory, and the orchestrator lock. Lives next to the show production
    path so any lab machine that mounts the share can boot the local stack.
    """

    return resolve_mapped_path(get_production_path() / ".telemetry")


def get_shared_telemetry_spool_dir() -> Path:
    """Return the shared production telemetry spool directory for this user/host."""

    try:
        username = getpass.getuser()
    except Exception:
        username = os.getenv("USER") or os.getenv("USERNAME") or ""
    host = socket.gethostname() or platform.node()
    safe_user = _sanitize_path_segment(username, fallback="unknown_user")
    safe_host = _sanitize_path_segment(host, fallback="unknown_host")
    return get_shared_telemetry_backend_dir() / "raw" / safe_host / safe_user


def get_rigging_path() -> Path:
    return get_character_path() / "Rigging"


def get_rig_build_path() -> Path:
    return get_production_path() / "rig_build"


def resolve_mapped_path(path: str | Path) -> Path:
    """Windows mapped drive workaround. Adapated from: https://bugs.python.org/msg309160"""
    path = Path(path).resolve()

    if platform.system() != "Windows":
        return path

    mapped_paths = []
    for drive in "ZYXWVUTSRQPONMLKJIHGFEDCBA":
        root = Path("{}:/".format(drive))
        try:
            mapped_paths.append(root / path.relative_to(root.resolve()))
        except (ValueError, OSError):
            pass
    return min(mapped_paths, key=lambda x: len(str(x)), default=path)


def _normalize_documentation_root(value: str) -> str:
    value = value.strip()
    if not value:
        return _DEFAULT_DOCUMENTATION_URL
    if "://" in value:
        return value.rstrip("/") + "/"

    doc_path = Path(value).expanduser()
    if not doc_path.is_absolute():
        doc_path = get_repo_root() / doc_path
    return str(doc_path.resolve())
