"""Generate the sandwich OCIO config + RenderMan JSON + README.

Run via `hython src/pipe/core/color/build.py`. Outputs land in
`/groups/sandwich/05_production/color_configuration/<version>/`.
"""

from __future__ import annotations

import argparse
import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path

from pipe.core.color import (
    ACTIVE_VIEWS,
    CONFIG_VERSION,
    DEFAULT_VIEW,
    DISPLAY,
    config_dir,
)

DEFAULT_SOURCE_URI = "ocio://cg-config-v1.0.0_aces-v1.3_ocio-v2.1"

# Pre-baked display-rendering LUTs grafted onto sRGB - Display. Neither OpenDRT
# nor the ACES 2.0 DRT can be evaluated by OCIO 2.3 (the DCC fleet), so each is
# a 3D LUT sampled once by bake.py (ACEScct-encoded input -> display-linear
# Rec.709). See context/color.md and context/adr/0003-opendrt-show-look.md.
LUTS_DIRNAME = "luts"
OPENDRT_LUT = "opendrt_standard.cube"
ACES2_LUT = "aces2_sdr_rec709.cube"

# (view name, backing colorspace, LUT, monochrome?, encode). View names are the
# menu strings and must match ACTIVE_VIEWS in pipe.core.color verbatim.
_SHOW_VIEWS = (
    (
        "OpenDRT - Standard",
        "OpenDRT Standard - Display",
        OPENDRT_LUT,
        False,
        "power2.2",
    ),
    (
        "OpenDRT - Standard B&W",
        "OpenDRT Standard B&W - Display",
        OPENDRT_LUT,
        True,
        "power2.2",
    ),
    (
        "ACES 2.0 - SDR 100 nits (Rec.709)",
        "ACES 2.0 SDR - Display",
        ACES2_LUT,
        False,
        "srgb",
    ),
    (
        "ACES 2.0 - SDR B&W (Rec.709)",
        "ACES 2.0 SDR B&W - Display",
        ACES2_LUT,
        True,
        "srgb",
    ),
)

# Rec.709 luminance weights; a saturation-0 matrix built from these collapses a
# display-linear image to its luma (the B&W lighting views).
_REC709_LUMA = (0.2126, 0.7152, 0.0722)

# Pixar's stock keys for RfH's pxrtexture `filename_colorspace` dropdown.
_RMAN_OCIO_ALIASES = {
    "rendering": "acescg",
    "srgb_texture": "srgbtex",
    "srgb_linear": "srgblin",
    "data": "data",
}


def _load_ocio():
    try:
        import PyOpenColorIO as ocio
    except Exception as exc:
        raise RuntimeError("PyOpenColorIO required — run with hython.") from exc
    return ocio


def _resolve_colorspace(config, candidates: tuple[str, ...]) -> str:
    known = list(config.getColorSpaceNames())
    lower = {name.lower(): name for name in known}
    for cand in candidates:
        if cand in known:
            return cand
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    raise ValueError(f"Cannot resolve colorspace from {candidates}.")


def _resolve_core_spaces(config) -> tuple[str, str, str, str, str, str]:
    return (
        _resolve_colorspace(config, ("ACES - ACEScg", "ACEScg")),
        _resolve_colorspace(config, ("sRGB - Texture", "Utility - sRGB - Texture")),
        _resolve_colorspace(
            config,
            (
                "Linear Rec.709 (sRGB)",
                "Utility - Linear - sRGB",
                "Utility - Linear - Rec.709",
            ),
        ),
        _resolve_colorspace(config, ("Raw", "Utility - Raw")),
        _resolve_colorspace(config, ("ACEScct", "ACES - ACEScct")),
        _resolve_colorspace(config, ("ACES2065-1", "ACES - ACES2065-1")),
    )


def _display_encode(ocio, style: str):
    # display-linear -> code values. OpenDRT's canonical sRGB output is a pure
    # 2.2 power (matches the reviewed look); ACES 2.0's is the piecewise sRGB
    # curve of its own config.
    if style == "power2.2":
        t = ocio.ExponentTransform()
        t.setValue([2.2, 2.2, 2.2, 1.0])
        t.setNegativeStyle(ocio.NEGATIVE_CLAMP)
        t.setDirection(ocio.TRANSFORM_DIR_INVERSE)
        return t
    t = ocio.ExponentWithLinearTransform()
    t.setGamma([2.4, 2.4, 2.4, 1.0])
    t.setOffset([0.055, 0.055, 0.055, 0.0])
    t.setNegativeStyle(ocio.NEGATIVE_LINEAR)
    t.setDirection(ocio.TRANSFORM_DIR_INVERSE)
    return t


def _saturation_zero_matrix(ocio):
    r, g, b = _REC709_LUMA
    mt = ocio.MatrixTransform()
    mt.setMatrix([r, g, b, 0, r, g, b, 0, r, g, b, 0, 0, 0, 0, 1])
    return mt


def _add_show_views(ocio, config, reference: str, acescct: str) -> None:
    # Each view is a display colorspace whose from_reference chain is
    # ACEScct-encode (the LUT's input shaper) -> 3D LUT (-> display-linear) ->
    # [saturation-0 for B&W] -> display encode.
    for view_name, cs_name, lut, mono, encode in _SHOW_VIEWS:
        group = ocio.GroupTransform()
        group.appendTransform(ocio.ColorSpaceTransform(src=reference, dst=acescct))
        group.appendTransform(
            ocio.FileTransform(
                src=f"{LUTS_DIRNAME}/{lut}", interpolation=ocio.INTERP_TETRAHEDRAL
            )
        )
        if mono:
            group.appendTransform(_saturation_zero_matrix(ocio))
        group.appendTransform(_display_encode(ocio, encode))

        cs = ocio.ColorSpace(ocio.REFERENCE_SPACE_SCENE, cs_name)
        cs.setFamily("Display")
        cs.setTransform(group, ocio.COLORSPACE_DIR_FROM_REFERENCE)
        config.addColorSpace(cs)
        config.addDisplayView(DISPLAY, view_name, cs_name)


def build_config(ocio, source_uri: str):
    config = ocio.Config.CreateFromFile(source_uri)
    config.setName(CONFIG_VERSION)
    config.setDescription(
        f"sandwich-pipeline OCIO config ({CONFIG_VERSION}). "
        f"Generated from {source_uri}. See context/color.md."
    )

    acescg, srgb_texture, linear_srgb, raw, acescct, aces2065_1 = _resolve_core_spaces(
        config
    )

    config.setRole("scene_linear", acescg)
    config.setRole("rendering", acescg)
    config.setRole("compositing_linear", acescg)
    config.setRole("default", raw)
    config.setRole("data", raw)
    # Pixar's RfH docs require `srgb_linear` to always be defined.
    config.setRole("srgb_linear", linear_srgb)
    config.setRole("color_picking", srgb_texture)
    config.setRole("matte_paint", srgb_texture)
    config.setRole("texture_paint", srgb_texture)
    config.setRole("aces_interchange", aces2065_1)
    config.setRole("cie_xyz_d65_interchange", "CIE-XYZ-D65")
    config.setRole("color_timing", acescct)
    config.setRole("reference", aces2065_1)

    config.setRole("substance_3d_painter_standard_srgb", srgb_texture)
    config.setRole("substance_3d_painter_bitmap_import_8bit", srgb_texture)
    config.setRole("substance_3d_painter_bitmap_import_16bit", srgb_texture)
    config.setRole("substance_3d_painter_bitmap_import_floating", linear_srgb)
    config.setRole("substance_3d_painter_substance_material", srgb_texture)
    config.setRole("substance_3d_painter_bitmap_export_8bit", srgb_texture)
    config.setRole("substance_3d_painter_bitmap_export_16bit", srgb_texture)
    config.setRole("substance_3d_painter_bitmap_export_floating", linear_srgb)

    # Snake-case aliases on each common canonical
    for canonical, alias in (
        (acescg, "acescg"),
        (srgb_texture, "srgb_texture"),
        (linear_srgb, "linear_srgb"),
        (raw, "raw"),
    ):
        cs = config.getColorSpace(canonical)
        cs.addAlias(alias)
        config.addColorSpace(cs)

    rules = config.getFileRules()
    # Painter's `$colorSpace` filename token emits canonical OCIO names
    # verbatim (spaces, parens, dots and all). These patterns match
    # Painter's actual export filenames so OCIO-aware consumers (Nuke,
    # RV, non-pxrtexture nodes) resolve published .tex/.png correctly.
    rules.insertRule(0, "raw-suffix", raw, "*_Raw*", "*")
    rules.insertRule(
        1, "linear-rec709-suffix", linear_srgb, "*_Linear Rec.709 (sRGB)*", "*"
    )
    rules.insertRule(2, "srgb-texture-suffix", srgb_texture, "*_sRGB - Texture*", "*")
    rules.insertRule(3, "acescg-suffix", acescg, "*_ACEScg*", "*")
    rules.insertRule(4, "exr", acescg, "*", "exr")
    rules.insertRule(5, "png", srgb_texture, "*", "png")
    rules.insertRule(6, "jpg", srgb_texture, "*", "jpg")
    rules.insertRule(7, "jpeg", srgb_texture, "*", "jpeg")
    rules.insertRule(8, "tif", srgb_texture, "*", "tif")
    rules.insertRule(9, "tiff", srgb_texture, "*", "tiff")
    rules.insertRule(10, "hdr", linear_srgb, "*", "hdr")
    rules.setDefaultRuleColorSpace(raw)

    # Graft the OpenDRT + ACES 2.0 views (and their B&W variants). The LUTs they
    # reference (luts/*.cube) are copied next to config.ocio by main().
    _add_show_views(ocio, config, aces2065_1, acescct)

    config.setSearchPath(".")
    config.setActiveDisplays(DISPLAY)
    config.setActiveViews(ACTIVE_VIEWS)

    config.validate()
    return config


def _git_sha(script_path: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(script_path.parent),
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def _build_readme(source_uri: str, script_path: Path) -> str:
    return (
        f"# sandwich-pipeline OCIO config — {CONFIG_VERSION}\n\n"
        f"**Generated:** {datetime.datetime.now().isoformat(timespec='seconds')} "
        f"by `src/pipe/core/color/build.py` @ commit `{_git_sha(script_path)}`\n"
        f"**Source URI:** `{source_uri}`\n"
        f"**Working space:** ACEScg (ACES 1.3 CG)\n"
        f"**Default display / view:** {DISPLAY} / {DEFAULT_VIEW}\n"
        f"**Grafted views:** OpenDRT Standard + ACES 2.0 SDR (and B&W) via "
        f"`{LUTS_DIRNAME}/`; regenerate LUTs with `python src/pipe/core/color/bake.py`.\n\n"
        f"Do not edit by hand. Regenerate via `hython src/pipe/core/color/build.py`.\n"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the sandwich OCIO config, RenderMan JSON, and metadata README."
    )
    parser.add_argument("--source", default=DEFAULT_SOURCE_URI)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Override output dir (default: production path).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    ocio = _load_ocio()

    output_dir: Path = args.output if args.output is not None else config_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    config = build_config(ocio, args.source)
    (output_dir / "config.ocio").write_text(config.serialize(), encoding="utf-8")
    # Ship the committed display LUTs alongside config.ocio (search path ".").
    lut_src = Path(__file__).resolve().parent / LUTS_DIRNAME
    lut_dst = output_dir / LUTS_DIRNAME
    lut_dst.mkdir(exist_ok=True)
    for lut in (OPENDRT_LUT, ACES2_LUT):
        shutil.copyfile(lut_src / lut, lut_dst / lut)
    # RfH looks up this file by the config dir's name
    (output_dir / f"rman_color_config_{CONFIG_VERSION}.json").write_text(
        json.dumps({"ocio_aliases": _RMAN_OCIO_ALIASES}, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(
        _build_readme(args.source, Path(__file__).resolve()),
        encoding="utf-8",
    )

    sys.stdout.write(f"Wrote {output_dir}/\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
