from __future__ import annotations

from pipe.core.playblast import ScratchEntity, ShotGridDestination
from pipe.core.util.users import resolve_artist_display_name
from pipe.dcc.maya.assetfile import AssetMetadata
from pipe.dcc.maya.playblast.turnaround.config import Elevation, TurnaroundPass
from pipe.dcc.maya.playblast.turnaround.dialog import AssetTurnaroundDialog


class AnimTurnaroundDialog(AssetTurnaroundDialog):
    """Turnaround dialog for animation scratch scenes (pose library reviews).

    Scratch scenes carry no asset or shot metadata, so a ShotGrid upload
    creates a project-level Version — undiscoverable outside a review
    playlist, hence `playlist_required` on the clip's ShotGrid row.
    """

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

    def _clip_shotgrid(self) -> ShotGridDestination:
        # Falls back to the scene name; never None, so an upload row is
        # always offered even without metadata.
        return ShotGridDestination(
            entity=ScratchEntity(self._asset_display_name()),
            artist_display_name=resolve_artist_display_name().strip() or None,
            playlist_required=True,
        )


__all__ = ["AnimTurnaroundDialog"]
