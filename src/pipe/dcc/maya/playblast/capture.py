from __future__ import annotations

from mayacapture.capture import capture  # type: ignore[import-not-found]

from pipe.dcc.maya.playblast.viewport import ViewportQuality, capture_kwargs


def capture_frames(
    filename: str,
    camera: str | None,
    start_frame: int,
    end_frame: int,
    *,
    quality: ViewportQuality,
    resolution: tuple[int, int],
) -> None:
    """Render `[start_frame, end_frame]` through `camera` to PNGs under
    `filename`. `camera` of None leaves the active panel's camera in place."""
    width, height = resolution
    capture(
        width=width,
        height=height,
        filename=filename,
        start_frame=start_frame,
        end_frame=end_frame,
        camera=camera,
        format="image",
        compression="png",
        off_screen=True,
        # HUD burns onto the frames afterward (in the `Playblaster` base), not
        # during capture.
        show_ornaments=False,
        overwrite=True,
        maintain_aspect_ratio=False,
        viewer=0,
        **capture_kwargs(quality),
    )


__all__ = ["capture_frames"]
