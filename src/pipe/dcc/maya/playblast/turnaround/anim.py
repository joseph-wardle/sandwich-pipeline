from __future__ import annotations

from pipe.core.playblast.review import PlayblastEntity
from pipe.dcc.maya.assetfile import AssetMetadata
from pipe.dcc.maya.playblast.turnaround.config import Elevation, TurnaroundPass
from pipe.dcc.maya.playblast.turnaround.dialog import AssetTurnaroundDialog


class AnimTurnaroundDialog(AssetTurnaroundDialog):
    """Turnaround dialog for animation scratch scenes (pose library reviews).

    Scratch scenes carry no asset or shot metadata, so a ShotGrid upload
    creates a project-level Version — attached to nothing but the selected
    review playlist. "Upload as new asset version" is therefore not offered;
    review upload is the only mode.
    """

    WINDOW_TITLE = "SKD Anim Turnaround"
    SUBTITLE = "Capture a turnaround review movie of the current pose"
    SOURCE_VALUE = "Current Maya Scene"
    SOURCE_TOOLTIP = "Uses the current Maya scene and current selection."
    SUBJECT_LABEL = "Scene"
    HUD_ASSET_DETAILS = False
    UPLOAD_TOOLTIP = (
        "Upload the turnaround movie to a ShotGrid review playlist for dailies."
    )
    ELEVATIONS = (Elevation.LEVEL, Elevation.THREE_QUARTER)
    WIREFRAME_PASSES = False
    DEFAULT_UI_PASSES = (TurnaroundPass(Elevation.LEVEL, False),)
    ALLOW_VERSION_UPLOAD = False
    REVIEW_DISABLE_HINT = "'Upload to ShotGrid'"

    def _read_asset_metadata(self) -> AssetMetadata | None:
        # Scratch scenes have no pipeline metadata; skip the read so it
        # doesn't log a scary (and expected) resolution failure.
        return None

    def _upload_entity(self) -> PlayblastEntity:
        # Falls back to the scene name; never None, so uploads are always
        # possible even without metadata.
        return PlayblastEntity.scratch(self._asset_display_name())


__all__ = ["AnimTurnaroundDialog"]
