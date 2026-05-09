"""Fail-open local site customization.

When a project-local ``.venv`` exists, add its site-packages to ``sys.path``.
If not present, do nothing (never raise during interpreter startup).
"""

import site
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parents[1]
_VENV_CFG = ROOT / ".venv" / "pyvenv.cfg"


def _read_venv_version(cfg_path: Path) -> Optional[str]:
    try:
        lines = cfg_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        if not line.startswith("version_info"):
            continue
        try:
            version_str = line.split("=", 1)[1].strip()
        except Exception:
            continue
        parts = version_str.split(".", 2)
        if len(parts) < 2:
            continue
        return f"python{parts[0]}.{parts[1]}"
    return None


_venv_version = _read_venv_version(_VENV_CFG)
if _venv_version:
    _sitedir = ROOT / ".venv" / "lib" / _venv_version / "site-packages"
    if _sitedir.is_dir():
        site.addsitedir(str(_sitedir))
