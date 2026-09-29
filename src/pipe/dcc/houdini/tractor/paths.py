"""Output paths: the scene decides what to render, Send decides where.

Send writes every output's per-frame path into `render.usd`, so the file alone
says where a job's frames go, and a frame re-rendered by hand lands there too.
Each RenderProduct and PxrCryptomatte filter of the rendered settings writes
into a folder named after its prim, beside `render.usd`. A product that is
denoised renders into `tmp/` instead, and Denoise fills its folder.
"""

from __future__ import annotations

import re
from pathlib import Path

from pxr import Sdf, Usd, UsdRender

from pipe.dcc.houdini.tractor import SendRefused

# Written by the USD ROP inside Configure.
RENDER_USD = "render.usd"
# Written by Cleanup once it found every output and cleared tmp/; later reruns
# don't change it. With Remove Encoded Frames on, the encoded frames' folder is gone.
COMPLETE = "complete"
# Scratch that no finished output depends on.
TMP = "tmp"
# Folders of their own in tmp/, beside the raw frames of the denoised product.
DENOISED = "denoised"
ENCODE = "encode"
# Where versions from before each output had a folder of its own keep their
# frames. Comp and fx2d read an unfinished version holding one as such a version.
LEGACY_DIRS = ("images_dn", "images")

CAMERA = "Camera"
SETTINGS = "RenderSettings"
PRODUCT = "RenderProduct"
VAR = "RenderVar"
CRYPTOMATTE = "PxrCryptomatte"

CRYPTOMATTE_PRODUCT = "cryptomatte"
INLINE_FILTER = re.compile(r"ri:samplefilter\d+:name")
XPU = "Xpu"
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


def cryptomattes(
    settings: UsdRender.Settings, products: list[Usd.Prim], renderer: str
) -> list[Usd.Prim]:
    prim = settings.GetPrim()
    if any(
        INLINE_FILTER.fullmatch(a.GetName()) and a.Get() == CRYPTOMATTE
        for a in prim.GetAttributes()
    ):
        raise SendRefused(
            f"{settings.GetPath()} chooses PxrCryptomatte as a Sample Filter. XPU "
            "ignores it, and Send can't put its frames in the version folder. Set "
            "that Sample Filter to None, and choose Cryptomatte on the render layer."
        )
    rel = prim.GetRelationship(SAMPLE_FILTERS)
    found = _targets(rel) if rel else []
    filters = [prim for prim in found if prim.GetTypeName() == CRYPTOMATTE]
    as_products = [
        p
        for p in products
        if UsdRender.Product(p).GetProductTypeAttr().Get() == CRYPTOMATTE_PRODUCT
    ]
    if filters and as_products:
        raise SendRefused(
            f"{settings.GetPath()} renders Cryptomatte both as the product "
            f"{as_products[0].GetPath()} and as the filter {filters[0].GetPath()}, "
            "and on XPU the product stops the filter writing anything. Remove the "
            "Cryptomatte product; the filter works on RIS and XPU."
        )
    # The others write nothing, and husk still exits 0.
    if len(filters) > 1 and XPU in renderer:
        raise SendRefused(
            f"{settings.GetPath()} has {len(filters)} Cryptomatte filters, but XPU "
            "writes only the last. Keep one, and render the others in another "
            "layer or with RIS."
        )
    # XPU's Cryptomatte writer crashes husk on every frame.
    if filters and XPU in renderer and any(_overscan(settings, p) for p in products):
        raise SendRefused(
            f"{settings.GetPath()} renders Cryptomatte with overscan, which crashes "
            "XPU. Set Overscan on Configure to 0, or render with RIS."
        )
    return filters


def _overscan(settings: UsdRender.Settings, product: Usd.Prim) -> bool:
    attr = UsdRender.Product(product).GetDataWindowNDCAttr()
    # A product without its own data window renders the settings'.
    if not attr.HasAuthoredValue():
        attr = settings.GetDataWindowNDCAttr()
    x0, y0, x1, y1 = attr.Get()
    return x0 < 0 or y0 < 0 or x1 > 1 or y1 > 1


def resolution(
    settings: UsdRender.Settings, products: list[Usd.Prim]
) -> tuple[int, int]:
    """What husk renders at: the first product's own resolution, else the settings'."""
    authored = [
        attr
        for p in products
        if (attr := UsdRender.Product(p).GetResolutionAttr()).HasAuthoredValue()
    ]
    x, y = (authored[0] if authored else settings.GetResolutionAttr()).Get()
    return int(x), int(y)


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


def beauty_product(settings: UsdRender.Settings, products: list[Usd.Prim]) -> Usd.Prim:
    """The product Denoise and Encode read: the one with a beauty var.

    RenderMan fills R, G, B with whichever var comes first, so a product
    without a beauty var would make a movie of another pass.
    """
    found = [p for p in products if beauty(p)]
    if len(found) != 1:
        names = ", ".join(str(p.GetPath()) for p in found) or "none"
        raise SendRefused(
            f"Denoise and Encode read the one render product of "
            f"{settings.GetPath()} with a beauty (Ci) render var, but found: "
            f"{names}. Add the beauty to one product, or remove Denoise and Encode."
        )
    return found[0]


def author_outputs(
    stage: Usd.Stage, frames: list[int], outputs: list[Usd.Prim], to_tmp: list[Usd.Prim]
) -> list[Path]:
    """Point each output at its folder, save, and return the folders husk writes."""
    names = [prim.GetName() for prim in outputs]
    for name in names:
        if name in (TMP, DENOISED, ENCODE, COMPLETE):
            raise SendRefused(
                f'Send keeps a file or folder of its own named "{name}", so no '
                "output can have that name. Rename the render product or "
                "Cryptomatte filter."
            )
        if name in LEGACY_DIRS:
            raise SendRefused(
                f'An output named "{name}" would make comp read this version\'s '
                "frames while they still render. Rename the render product or "
                "Cryptomatte filter."
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
