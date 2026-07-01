"""Generate the sandwich OCIO config + RenderMan JSON + README.

Run via `hython src/core/color/build.py`. Outputs land in
`/groups/sandwich/05_production/color_configuration/<version>/`.
"""

from __future__ import annotations

import argparse
import datetime
import json
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
        f"by `src/core/color/build.py` @ commit `{_git_sha(script_path)}`\n"
        f"**Source URI:** `{source_uri}`\n"
        f"**Working space:** ACEScg (ACES 1.3 CG)\n"
        f"**Default display / view:** {DISPLAY} / {DEFAULT_VIEW}\n\n"
        f"Do not edit by hand. Regenerate via `hython src/core/color/build.py`.\n"
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
