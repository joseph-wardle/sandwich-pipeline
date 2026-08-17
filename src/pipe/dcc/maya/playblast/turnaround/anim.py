from __future__ import annotations

from pipe.dcc.maya.assetfile import AssetMetadata
from pipe.dcc.maya.playblast.turnaround.config import Elevation, TurnaroundPass
from pipe.dcc.maya.playblast.turnaround.dialog import AssetTurnaroundDialog


class AnimTurnaroundDialog(AssetTurnaroundDialog):
    """Turnaround dialog for animation scratch scenes (pose library reviews)."""

    WINDOW_TITLE = "SKD Anim Turnaround"
    SOURCE_VALUE = "Current Maya Scene"
    SOURCE_TOOLTIP = "Uses the current Maya scene and current selection."
    SUBJECT_LABEL = "Scene"
    HUD_ASSET_DETAILS = False
    ELEVATIONS = (Elevation.LEVEL, Elevation.THREE_QUARTER)
    WIREFRAME_PASSES = False
    DEFAULT_UI_PASSES = (TurnaroundPass(Elevation.LEVEL, False),)

    SETTINGS_KEY = "maya_anim_turnaround"

    def _read_asset_metadata(self) -> AssetMetadata | None:
        # Scratch scenes have no pipeline metadata; skip the read so it
        # doesn't log a scary (and expected) resolution failure.
        return None


__all__ = ["AnimTurnaroundDialog"]
