"""Output paths: the scene decides what to render, Send decides where.

Send writes every output's per-frame path into `render.usd`, so the file alone
says where a job's frames go, and a frame re-rendered by hand lands there too.
Each RenderProduct and PxrCryptomatte filter writes into a folder named after
its prim, beside `render.usd`.
"""

from __future__ import annotations

from pathlib import Path

from pxr import Sdf, Usd, UsdRender

# Written by the USD ROP inside Configure.
RENDER_USD = "render.usd"

PRODUCT = "RenderProduct"
CRYPTOMATTE = "PxrCryptomatte"
FILENAME = "inputs:ri:filename"


def author_outputs(usd: Path, frames: list[int]) -> list[Path]:
    """Point every output in `usd` at its own folder and return the folders."""
    stage = Usd.Stage.Open(str(usd), Usd.Stage.LoadNone)
    outputs = [
        prim
        for prim in stage.Traverse()
        if prim.IsA(PRODUCT) or prim.GetTypeName() == CRYPTOMATTE
    ]
    # husk renders nothing without a product, and still exits 0.
    if not any(prim.IsA(PRODUCT) for prim in outputs):
        raise ValueError(f"{usd} has no RenderProduct, so it would render nothing.")
    folders: list[Path] = []
    for prim in outputs:
        if prim.IsA(PRODUCT):
            attr = UsdRender.Product(prim).CreateProductNameAttr()
        else:
            attr = prim.CreateAttribute(FILENAME, Sdf.ValueTypeNames.String)
        folder = usd.parent / prim.GetName()
        attr.Clear()
        for frame in frames:
            attr.Set(str(folder / f"{frame:04}.exr"), frame)
        folders.append(folder)
    stage.GetRootLayer().Save()
    return folders


def single_product_folder(usd: Path) -> Path:
    """The folder of the one RenderProduct in `usd`, which Denoise and Encode read."""
    stage = Usd.Stage.Open(str(usd), Usd.Stage.LoadNone)
    products = [prim for prim in stage.Traverse() if prim.IsA(PRODUCT)]
    if len(products) != 1:
        names = ", ".join(str(prim.GetPath()) for prim in products) or "none"
        raise ValueError(
            f"Denoise and Encode read one render product, but {usd} has: {names}."
        )
    return usd.parent / products[0].GetName()
