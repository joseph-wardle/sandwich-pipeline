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
# The deploy lives at <share>/.pipeline; 06_software is its sibling on the share.
SOFTWARE_DIR = REPO_ROOT.parent / "06_software"

sys.path.insert(0, str(SRC))
from env import Executables  # noqa: E402  (needs SRC on sys.path)


@dataclass(frozen=True)
class Launcher:
    filename: str  # installed .desktop name, e.g. "mayo.desktop"
    name: str  # menu display name, e.g. "Mayo"
    dcc: str  # `pipe` launch target, e.g. "maya"
    icon: str  # icon file in resources/icon/

    @property
    def generic_name(self) -> str:
        return f"Sandwich Kwon Do {self.dcc.capitalize()} Launcher"

    @property
    def exec_line(self) -> str:
        # -m pipe needs `src` on PYTHONPATH; the desktop environment does not
        # source the user's shell rc, so every path here must be absolute.
        return f"env PYTHONPATH={SRC} {VENV_PYTHON} -m pipe {self.dcc}"


@dataclass(frozen=True)
class ToolLauncher:
    """A standalone pipeline window. It runs in mayapy for its Qt binding,
    which the venv lacks; `src/sitecustomize.py` adds the venv's packages."""

    filename: str
    name: str
    module: str  # run with `python -m`, e.g. "pipe.core.render.manager"
    icon: str
    generic_name: str

    @property
    def exec_line(self) -> str:
        return f"env PYTHONPATH={SRC} {Executables.mayapy} -m {self.module}"


LAUNCHERS: list[Launcher | ToolLauncher] = [
    Launcher("mayo.desktop", "Mayo", "maya", "maya.png"),
    Launcher("panini.desktop", "Panini", "houdini", "houdini.png"),
    Launcher("microwave.desktop", "Microwave", "nuke", "nuke.png"),
    Launcher("toaster.desktop", "Toaster", "blender", "blender.png"),
    ToolLauncher(
        "render-manager.desktop",
        "Render Manager",
        "pipe.core.render.manager",
        "sandwich.png",
        "Sandwich Kwon Do Lighting Render Cleanup",
    ),
]


def render(launcher: Launcher | ToolLauncher) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={launcher.name}\n"
        f"GenericName={launcher.generic_name}\n"
        f"Exec={launcher.exec_line}\n"
        f"Icon={ICONS / launcher.icon}\n"
        f"StartupWMClass={launcher.name}\n"
        "StartupNotify=true\n"
        "Terminal=false\n"
    )


def _write_launchers(dest_dir: Path, verb: str) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for launcher in LAUNCHERS:
        target = dest_dir / launcher.filename
        # Replace any prior file or the old symlink-based launchers.
        if target.is_symlink() or target.exists():
            target.unlink()
        target.write_text(render(launcher), encoding="utf-8")
        print(f"{verb} {target}")


def install() -> None:
    """Primary path: put the launchers in the user's application menu."""
    _write_launchers(APPLICATIONS, "Installed")
    subprocess.run(["update-desktop-database", str(APPLICATIONS)], check=False)
    print("Updated desktop database")


def publish_to_software() -> None:
    """Deploy step: provision the shared 06_software folder.

    Writes the per-DCC launchers so artists can double-click them straight
    from the share, plus the installer entry that adds menu shortcuts. Run
    once from the deploy after redeploying (`... --software`).
    """
    _write_launchers(SOFTWARE_DIR, "Published")
    installer = SOFTWARE_DIR / "installer.desktop"
    installer.write_text(
        (REPO_ROOT / "desktop_launchers" / "installer.desktop").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    print(f"Published {installer}")


if __name__ == "__main__":
    if not VENV_PYTHON.exists():
        sys.exit(
            f"venv python not found at {VENV_PYTHON}; run `uv sync` in {REPO_ROOT} first"
        )
    if "--software" in sys.argv[1:]:
        publish_to_software()
    else:
        install()
