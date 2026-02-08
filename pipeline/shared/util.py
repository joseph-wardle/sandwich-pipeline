from __future__ import annotations

import importlib
import importlib.util
import os
import platform
import subprocess
from inspect import getmembers, isabstract, isclass
from pathlib import Path

from env import production_path as _prp

_DOCUMENTATION_ENV_VAR = "PIPELINE_DOCUMENTATION_URL"
_DEFAULT_DOCUMENTATION_URL = "https://github.com/joseph-wardle/sandwich-pipeline/wiki/"


def find_implementation(cls: type, module: str, package: str | None = None) -> type:
    """Find an implementation of the class in the specified module."""
    # Check if the specified module exists
    if importlib.util.find_spec(module, package):
        # Import the module
        imported_module = importlib.import_module(module, package)

        # Check if the submodule contains an implementation of the class
        classes = getmembers(
            imported_module,
            lambda obj: isclass(obj) and not isabstract(obj) and issubclass(obj, cls),
        )

        # Check if more or less than one implementation was found
        if len(classes) < 1:
            raise AssertionError(
                f"module '{module}' does not contain an "
                f"implementation of class '{cls.__name__}'"
            )
        elif len(classes) > 1:
            raise AssertionError(
                f"module '{module}' contains multiple "
                f"implementations of class '{cls.__name__}'"
            )

        # Return the implementing class
        return classes[0][1]

    else:
        raise ValueError(f"could not find module '{module}'")


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
            for item in get_pipe_path().parent.iterdir()
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


def get_pipe_path() -> Path:
    return Path(__file__).resolve().parents[1]


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


def get_rigging_path() -> Path:
    return get_character_path() / "Rigging"


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
        doc_path = get_pipe_path().parent / doc_path
    return str(doc_path.resolve())
