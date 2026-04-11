import sys
from pathlib import Path


def register():
    import addon_utils

    # For some reason the addons directory isn't loaded yet?
    # It'll be added to path by blender later but we need to enable the addon.
    sys.path.append(str(Path(__file__).resolve().parents[1] / "addons"))
    addon_utils.enable("pipeline")


def unregister():
    pass


if __name__ == "__main__":
    register()
