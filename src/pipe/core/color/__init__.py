"""Runtime entry point for the sandwich OCIO config."""

from __future__ import annotations

from pathlib import Path

from pipe.core.util.paths import get_production_path

CONFIG_VERSION = "sandwich-v02"

DISPLAY = "sRGB - Display"
DEFAULT_VIEW = "OpenDRT - Standard"
ACTIVE_VIEWS = (
    "OpenDRT - Standard, OpenDRT - Standard B&W, "
    "ACES 2.0 - SDR 100 nits (Rec.709), ACES 2.0 - SDR B&W (Rec.709), "
    "ACES 1.0 - SDR Video, Un-tone-mapped, Raw"
)


def config_dir() -> Path:
    """Production folder containing config.ocio and the RenderMan JSON."""
    return get_production_path() / "color_configuration" / CONFIG_VERSION


def config_path() -> Path:
    """The config.ocio artifact path."""
    return config_dir() / "config.ocio"


def ocio_env_vars() -> dict[str, str]:
    # Deliberately no OCIO_ACTIVE_VIEWS: a multi-value setting silently breaks
    # Nuke 16.0v4's OCIODisplay view enumeration (the Viewer falls back to Raw
    # only — Foundry bug 606354, fixed in 16.0v9).
    return {
        "OCIO": str(config_path()),
        "OCIO_ACTIVE_DISPLAYS": DISPLAY,
        # "OCIO_ACTIVE_VIEWS": ACTIVE_VIEWS,
        # Read by RenderMan to find `rman_color_config_<version>.json`.
        "RMAN_COLOR_CONFIG_DIR": str(config_dir()),
    }


__all__ = [
    "ACTIVE_VIEWS",
    "CONFIG_VERSION",
    "DEFAULT_VIEW",
    "DISPLAY",
    "config_dir",
    "config_path",
    "ocio_env_vars",
]
