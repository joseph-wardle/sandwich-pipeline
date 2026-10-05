"""Bring a scene saved before publish versions up to date as it opens.

Only *Sandwich Kwon Do* has such scenes. Please remove this module and its call
in `MAnimShotFileManager.run_on_open` for future productions.
"""

from __future__ import annotations

from pxr import Sdf

# Where the camera was published, as `shot/<code>/cam/cam.usd` or the same from `/job`.
_OLD_CAMERA = "/cam/cam.usd"


def upgrade(root_layer: Sdf.Layer) -> None:
    """Drop the sublayer on the camera's old file, which is gone.

    The scene then has no camera, so opening it sublayers the camera's current layer.
    """
    old = [path for path in root_layer.subLayerPaths if path.endswith(_OLD_CAMERA)]
    for path in old:
        root_layer.subLayerPaths.remove(path)
