"""RenderMan's `denoise_batch`: the product it reads and its config.

The beauty product, which carries the denoise passes, renders into `tmp/`. Each
frame's command denoises it and writes the finished frame into the product's folder.

The config names layers as RenderMan writes them, not as the vars are named:
with the product's asrgba on, the alpha var `a` is written as `A`, and the
beauty `Ci` as `R, G, B`.
"""

from __future__ import annotations

import json
from pathlib import Path

from pxr import Sdf, Usd, UsdRender

from pipe.core.render import DENOISE_CONFIG
from pipe.dcc.houdini.tractor import SendRefused, paths

ASRGBA = "driver:parameters:openexr:asrgba"

# RenderMan's symmetric multiframe denoiser.
TOPOLOGY = "${RMANTREE}/lib/denoise/full_w7_4sv2_sym_gen2.topo"
PARAMETERS = "${RMANTREE}/lib/denoise/20970-renderman.param"

# The vars Denoise adds that its config reads. The beauty is read as R, G, B,
# so it isn't among them.
PASSES = frozenset(
    {"a", "mse", "sampleCount", "albedo", "albedo_mse", "normal", "normal_mse"}
    | {"diffuse", "diffuse_mse", "specular", "specular_mse"}
)


def config(vars: list[str]) -> dict:
    """denoise_batch's config for a product with these vars.

    The finished beauty is denoised diffuse plus denoised specular, which
    denoise_batch sums into R, G, B.
    """
    outputs: dict[str, list[dict]] = {
        "albedo": [],
        "alpha": [_output("A", "A")],
        "diffuse": [_output("diffuse", "RGB")],
        "specular": [_output("specular", "RGB")],
        "copy": [],
    }
    for var in vars:
        # The denoiser's own inputs, the alpha and the beauty are written above.
        if var.endswith(("mse", "_var")) or var in ("a", "Ci", "sampleCount"):
            continue
        kind = var.partition("_")[0]
        if var.startswith(("backward", "forward")) or var in ("__depth", "zfiltered"):
            outputs["alpha"].append(_output(var, var))
        elif kind in ("albedo", "diffuse", "specular"):
            outputs[kind].append(_output(var, var))
        else:
            outputs["copy"].append(_output(var, var))

    passes = [
        _pass("albedo", "albedo", "albedo_mse", outputs["albedo"]),
        _pass("alpha", "A", "mse", outputs["alpha"]),
        _pass("diffuse", "diffuse", "diffuse_mse", outputs["diffuse"]),
        _pass("specular", "specular", "specular_mse", outputs["specular"]),
    ]
    if outputs["copy"]:
        # No layer is named Ci, so denoise_batch skips this pass's filter and
        # copies its outputs unchanged.
        passes.append(_pass("copy", "Ci", "mse", outputs["copy"]))

    return {
        "settings": {
            "albedo": _read("albedo"),
            "albedo_variance": _read("albedo_mse"),
            "normal": _read("normal"),
            "normal_variance": _read("normal_mse"),
            "sample_count": _read("sampleCount"),
            "frame-include": "${FrameInclude}",
            "frame-exclude": "${FrameExclude}",
            "topology": TOPOLOGY,
            "parameters": PARAMETERS,
            "asymmetry": 0.0,
            "overwrite": "OverwriteChannels",
            "progress": True,
            "tiles": [1, 1],
        },
        "passes": passes,
    }


def _read(layer: str) -> dict:
    return {"layer": layer, "filename": "${InputFile}"}


def _output(read: str, write: str) -> dict:
    return {"read": _read(read), "write": {"layer": write, "filename": "${OutputFile}"}}


def _pass(name: str, input: str, variance: str, outputs: list[dict]) -> dict:
    return {
        "name": name,
        "input": _read(input),
        "input_variance": _read(variance),
        "outputs": outputs,
    }


def write_config(folder: Path, config: dict) -> None:
    (folder / DENOISE_CONFIG).write_text(json.dumps(config))


def var_names(product: Usd.Prim) -> list[str]:
    return [paths.aov_name(var) for var in paths.render_vars(product)]


def add_passes(stage: Usd.Stage, configured: Usd.Stage, settings: str) -> None:
    """Append the render vars Denoise defined to the beauty product it denoises.

    `configured` is the stage before Denoise, so its vars are the artist's. Other
    products, such as Cryptomatte or another settings' utility passes, keep
    their own vars.
    """
    rendered = paths.rendered_settings(stage, settings)
    product = paths.beauty_product(rendered, paths.products(rendered))
    rel = UsdRender.Product(product).GetOrderedVarsRel()
    for prim in Usd.PrimRange(stage.GetPrimAtPath("/Render")):
        if prim.IsA(paths.VAR) and not configured.GetPrimAtPath(prim.GetPath()):
            rel.AddTarget(prim.GetPath())
    # The config reads the beauty and alpha as RGB and A. Without asrgba they are
    # written as Ci and a: the alpha is lost, and the copy pass filters the
    # utility passes instead of copying them. Render layers saved before it
    # defaulted on keep it off.
    product.CreateAttribute(ASRGBA, Sdf.ValueTypeNames.Int).Set(1)


def product(settings: UsdRender.Settings, products: list[Usd.Prim]) -> Usd.Prim:
    """The beauty product, which Denoise gave its passes."""
    found = paths.beauty_product(settings, products)
    if not PASSES <= set(var_names(found)):
        raise SendRefused(
            f"{found.GetPath()} lacks the passes Denoise adds, so it can't be "
            "denoised. Check that Denoise follows Configure and isn't bypassed."
        )
    return found
