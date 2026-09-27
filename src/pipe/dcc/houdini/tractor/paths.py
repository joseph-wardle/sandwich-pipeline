"""Output paths: the scene decides what to render, Send decides where.

Send writes every output's per-frame path into `render.usd`, so the file alone
says where a job's frames go, and a frame re-rendered by hand lands there too.
Each RenderProduct and PxrCryptomatte filter of the rendered settings writes
into a folder named after its prim, beside `render.usd`. A product that is
denoised renders into `tmp/` instead, and Denoise fills its folder.
"""

from __future__ import annotations

from pathlib import Path

from pxr import Sdf, Usd, UsdRender

from pipe.dcc.houdini.tractor import SendRefused

# Written by the USD ROP inside Configure.
RENDER_USD = "render.usd"
# Scratch that no finished output depends on.
TMP = "tmp"
# Folders of their own in tmp/, beside the raw frames of the denoised product.
DENOISED = "denoised"
ENCODE = "encode"

SETTINGS = "RenderSettings"
PRODUCT = "RenderProduct"
CRYPTOMATTE = "PxrCryptomatte"
FILENAME = "inputs:ri:filename"
SAMPLE_FILTERS = "ri:sampleFilters"
STATISTICS = "driver:parameters:aov:statistics"
# Denoise names its vars with the first; RenderMan for Houdini with the second.
AOV_NAMES = ("driver:parameters:aov:name", "driver:parameters:aov:husk:name")


def rendered_settings(stage: Usd.Stage, path: str) -> UsdRender.Settings:
    """The settings husk renders: `path`, else the stage's, else the only ones."""
    if path:
        # husk reads a bare name as relative to /Render.
        prim = stage.GetPrimAtPath(path if path.startswith("/") else f"/Render/{path}")
        if not (prim and prim.IsA(SETTINGS)):
            raise SendRefused(
                f"There are no render settings at {path}. Correct Render Settings "
                "on Configure, or turn it off."
            )
        return UsdRender.Settings(prim)
    if settings := UsdRender.Settings.GetStageRenderSettings(stage):
        return settings
    found = [prim for prim in stage.Traverse() if prim.IsA(SETTINGS)]
    # With several, husk ignores them all, renders a default image and exits 0.
    if len(found) != 1:
        raise SendRefused(
            f"The stage has {len(found)} render settings. Choose one with Render "
            "Settings on Configure."
        )
    return UsdRender.Settings(found[0])


def products(settings: UsdRender.Settings) -> list[Usd.Prim]:
    found = [p for p in _targets(settings.GetProductsRel()) if p.IsA(PRODUCT)]
    # husk renders nothing without a product, and still exits 0.
    if not found:
        raise SendRefused(
            f"{settings.GetPath()} has no render product, so nothing would be written."
        )
    return found


def cryptomattes(settings: UsdRender.Settings) -> list[Usd.Prim]:
    rel = settings.GetPrim().GetRelationship(SAMPLE_FILTERS)
    found = _targets(rel) if rel else []
    return [prim for prim in found if prim.GetTypeName() == CRYPTOMATTE]


def render_vars(product: Usd.Prim) -> list[Usd.Prim]:
    return _targets(UsdRender.Product(product).GetOrderedVarsRel())


def _targets(rel: Usd.Relationship) -> list[Usd.Prim]:
    # A target left behind by a deleted prim leads nowhere, and husk skips it too.
    stage = rel.GetStage()
    found = [stage.GetPrimAtPath(path) for path in rel.GetForwardedTargets()]
    return [prim for prim in found if prim]


def aov_name(var: Usd.Prim) -> str:
    return next(v for name in AOV_NAMES if (v := var.GetAttribute(name).Get()))


def beauty(product: Usd.Prim) -> str | None:
    """The AOV name of the product's beauty var, if it has one."""
    names = [
        aov_name(var)
        for var in render_vars(product)
        if var.GetAttribute("sourceName").Get() == "Ci"
        and not var.GetAttribute(STATISTICS).Get()
    ]
    return names[0] if names else None


def author_outputs(
    stage: Usd.Stage, frames: list[int], outputs: list[Usd.Prim], to_tmp: list[Usd.Prim]
) -> list[Path]:
    """Point each output at its folder, save, and return the folders husk writes."""
    names = [prim.GetName() for prim in outputs]
    for name in names:
        if name in (TMP, DENOISED, ENCODE):
            raise SendRefused(
                f'Send keeps scratch files in a folder named "{name}", so no output '
                "can have that name. Rename the render product or Cryptomatte filter."
            )
        if names.count(name) > 1:
            raise SendRefused(
                f'More than one output is named "{name}", and each writes into a '
                "folder of its name. Rename one of them."
            )
    version = Path(stage.GetRootLayer().realPath).parent
    folders: list[Path] = []
    for prim in outputs:
        if prim.IsA(PRODUCT):
            attr = UsdRender.Product(prim).CreateProductNameAttr()
        else:
            attr = prim.CreateAttribute(FILENAME, Sdf.ValueTypeNames.String)
        parent = version / TMP if prim in to_tmp else version
        folder = parent / prim.GetName()
        attr.Clear()
        for frame in frames:
            attr.Set(str(folder / f"{frame:04}.exr"), frame)
        folders.append(folder)
    stage.GetRootLayer().Save()
    return folders
