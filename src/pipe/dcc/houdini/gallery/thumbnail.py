"""Renders a gallery thumbnail straight from a published component USD."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from husd.utils import createFramedCameraToBounds
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdRender

from env import Executables

RENDERER = "HdPrmanLoaderRendererPlugin"
RESOLUTION = 256
MIN_SAMPLES = 32
MAX_SAMPLES = 128
PIXEL_VARIANCE = 0.02
TIME_LIMIT_SECONDS = 60
CAMERA_PATH = "/__thumbnail_camera__"
LIGHT_PATH = "/__thumbnail_light__"
SETTINGS_PATH = "/__thumbnail_settings__"
KEY_INTENSITY = 250.0
KEY_ANGLE = 10.0

HUSK = Executables.hython.with_stem("husk")


class ThumbnailRenderError(RuntimeError):
    pass


def render(export_path: Path, output: Path) -> bytes:
    """Render `export_path` to the PNG `output` and return its bytes.

    `output` is only touched once the render has succeeded, so a failed
    render leaves the previous thumbnail in place.
    """
    with tempfile.TemporaryDirectory(prefix="gallery_thumbnail_") as scratch:
        scene = Path(scratch) / f"{export_path.stem}.usda"
        rendered = Path(scratch) / output.name
        _write_scene(export_path, scene)
        completed = subprocess.run(
            [
                str(HUSK),
                str(scene),
                f"--renderer={RENDERER}",
                "--res",
                str(RESOLUTION),
                str(RESOLUTION),
                "--settings",
                SETTINGS_PATH,
                "--timelimit",
                str(TIME_LIMIT_SECONDS),
                "-o",
                str(rendered),
            ],
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0 or not rendered.is_file():
            raise ThumbnailRenderError(
                f"husk exited {completed.returncode} rendering {export_path}: "
                f"{completed.stderr.strip()[-500:]}"
            )
        data = rendered.read_bytes()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    return data


def _write_scene(export_path: Path, scene: Path) -> None:
    # husk still exits 0 when a sublayer fails to load, and renders an empty
    # frame, so the component has to be checked here.
    if Sdf.Layer.FindOrOpen(str(export_path)) is None:
        raise ThumbnailRenderError(f"{export_path} is missing or not a USD file")
    stage = Usd.Stage.CreateInMemory()
    stage.GetRootLayer().subLayerPaths.append(str(export_path))

    purposes = [UsdGeom.Tokens.default_, UsdGeom.Tokens.proxy, UsdGeom.Tokens.render]
    bounds = (
        UsdGeom.BBoxCache(Usd.TimeCode(1.0), purposes)
        .ComputeLocalBound(stage.GetPseudoRoot())
        .GetRange()
    )
    if bounds.IsEmpty():
        raise ThumbnailRenderError(f"{export_path} has no geometry to frame")
    createFramedCameraToBounds(stage, bounds, CAMERA_PATH)

    # A distant light aligned with the camera lights whatever it sees. Dome
    # lights speckle badly in hdPrman at this sample count.
    camera = UsdGeom.Camera(stage.GetPrimAtPath(CAMERA_PATH))
    key = UsdLux.DistantLight.Define(stage, LIGHT_PATH)
    key.CreateIntensityAttr(KEY_INTENSITY)
    key.CreateAngleAttr(KEY_ANGLE)
    UsdGeom.Xformable(key).AddTransformOp().Set(camera.GetLocalTransformation())

    settings = UsdRender.Settings.Define(stage, SETTINGS_PATH)
    settings.CreateCameraRel().SetTargets([CAMERA_PATH])
    settings.CreateResolutionAttr(Gf.Vec2i(RESOLUTION, RESOLUTION))
    prim = settings.GetPrim()
    prim.CreateAttribute("ri:hider:minsamples", Sdf.ValueTypeNames.Int).Set(MIN_SAMPLES)
    prim.CreateAttribute("ri:hider:maxsamples", Sdf.ValueTypeNames.Int).Set(MAX_SAMPLES)
    prim.CreateAttribute("ri:Ri:PixelVariance", Sdf.ValueTypeNames.Float).Set(
        PIXEL_VARIANCE
    )

    stage.GetRootLayer().Export(str(scene))


__all__ = ["ThumbnailRenderError", "render"]
