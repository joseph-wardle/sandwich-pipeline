"""Generate and install the Sandwich Kwon Do DCC launchers.

Run from the "Install Sandwich DCCs" entry in ``06_software``, or directly:

    uv run python install_desktop_launchers.py
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
SRC = REPO_ROOT / "src"
ICONS = REPO_ROOT / "resources" / "icon"
APPLICATIONS = Path.home() / ".local" / "share" / "applications"


@dataclass(frozen=True)
class Launcher:
    filename: str  # installed .desktop name, e.g. "mayo.desktop"
    name: str  # menu display name, e.g. "Mayo"
    dcc: str  # `pipe` launch target, e.g. "maya"
    icon: str  # icon file in resources/icon/


LAUNCHERS = [
    Launcher("mayo.desktop", "Mayo", "maya", "maya.png"),
    Launcher("panini.desktop", "Panini", "houdini", "houdini.png"),
    Launcher("microwave.desktop", "Microwave", "nuke", "nuke.png"),
    Launcher("toaster.desktop", "Toaster", "blender", "blender.png"),
]


def render(launcher: Launcher) -> str:
    # -m pipe needs `src` on PYTHONPATH; the desktop environment does not
    # source the user's shell rc, so every path here must be absolute.
    exec_line = f"env PYTHONPATH={SRC} {VENV_PYTHON} -m pipe {launcher.dcc}"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={launcher.name}\n"
        f"GenericName=Sandwich Kwon Do {launcher.dcc.capitalize()} Launcher\n"
        f"Exec={exec_line}\n"
        f"Icon={ICONS / launcher.icon}\n"
        f"StartupWMClass={launcher.name}\n"
        "StartupNotify=true\n"
        "Terminal=false\n"
    )


def install() -> None:
    APPLICATIONS.mkdir(parents=True, exist_ok=True)
    for launcher in LAUNCHERS:
        target = APPLICATIONS / launcher.filename
        # Replace any prior install, including the old symlink-based ones.
        if target.is_symlink() or target.exists():
            target.unlink()
        target.write_text(render(launcher), encoding="utf-8")
        print(f"Installed {target}")
    subprocess.run(["update-desktop-database", str(APPLICATIONS)], check=False)
    print("Updated desktop database")


if __name__ == "__main__":
    if not VENV_PYTHON.exists():
        sys.exit(
            f"venv python not found at {VENV_PYTHON}; run `uv sync` in {REPO_ROOT} first"
        )
    install()
