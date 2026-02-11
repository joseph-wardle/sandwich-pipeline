#!/usr/bin/env python3
"""Generate the sandwich-v01 OCIO config from the ACEScg built-in config.

This script creates a reproducible OCIO v2 config under
`pipeline/lib/ocio/sandwich-v01/config.ocio` with roles tailored for the
Substance Painter workflow:

- sRGB textures for 8/16-bit bitmap import/export
- Linear sRGB for floating-point bitmap import/export
- ACEScg for rendering/scene-linear

Run with a Python that provides PyOpenColorIO, e.g.:

    hython pipeline/tools/ocio/build_sandwich_config.py --force-exit

The `--force-exit` option is useful when running under hython to avoid a
known shutdown crash in some Houdini builds.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, Tuple

DEFAULT_SOURCE_URI = "ocio://cg-config-v1.0.0_aces-v1.3_ocio-v2.1"


def _load_ocio():
    try:
        import PyOpenColorIO as ocio  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "PyOpenColorIO is required. Run this script with a Python "
            "environment that provides it (for example, 'hython'), or "
            "install the OpenColorIO Python bindings."
        ) from exc
    return ocio


def _resolve_colorspace(config, candidates: Iterable[str]) -> str:
    names = list(config.getColorSpaceNames())
    lower_names = {name.lower(): name for name in names}

    for cand in candidates:
        if cand in names:
            return cand
    for cand in candidates:
        lowered = cand.lower()
        if lowered in lower_names:
            return lower_names[lowered]

    sample = ", ".join(names[:12])
    raise ValueError(
        "Could not resolve colorspace from candidates: "
        f"{', '.join(candidates)}. Sample available: {sample}"
    )


def _resolve_core_spaces(config) -> Tuple[str, str, str, str]:
    acescg = _resolve_colorspace(config, ["ACES - ACEScg", "ACEScg"])
    srgb_texture = _resolve_colorspace(
        config,
        [
            "Utility - sRGB - Texture",
            "sRGB - Texture",
            "Utility - sRGB Texture",
        ],
    )
    linear_srgb = _resolve_colorspace(
        config,
        [
            "Utility - Linear - sRGB",
            "Utility - Linear - Rec.709",
            "Linear Rec.709 (sRGB)",
        ],
    )
    raw_space = _resolve_colorspace(config, ["Utility - Raw", "Raw", "raw"])
    return acescg, srgb_texture, linear_srgb, raw_space


def build_config(ocio, source_uri: str):
    config = ocio.Config.CreateFromFile(source_uri)
    config.setName("sandwich-v01")
    config.setDescription(
        "Sandwich pipeline OCIO config (v01). Based on ACEScg cg-config with "
        "Substance Painter roles set to sRGB/Linear workflows."
    )

    acescg, srgb_texture, linear_srgb, raw_space = _resolve_core_spaces(config)

    # Core roles
    config.setRole("data", raw_space)
    config.setRole("scene_linear", acescg)
    config.setRole("rendering", acescg)
    config.setRole("compositing_linear", acescg)
    config.setRole("color_picking", srgb_texture)
    config.setRole("matte_paint", srgb_texture)
    config.setRole("texture_paint", srgb_texture)
    config.setRole("default", acescg)

    # Substance Painter roles
    config.setRole("substance_3d_painter_standard_srgb", srgb_texture)
    config.setRole("substance_3d_painter_bitmap_import_8bit", srgb_texture)
    config.setRole("substance_3d_painter_bitmap_import_16bit", srgb_texture)
    config.setRole("substance_3d_painter_bitmap_import_floating", linear_srgb)
    config.setRole("substance_3d_painter_substance_material", srgb_texture)
    config.setRole("substance_3d_painter_bitmap_export_8bit", srgb_texture)
    config.setRole("substance_3d_painter_bitmap_export_16bit", srgb_texture)
    config.setRole("substance_3d_painter_bitmap_export_floating", linear_srgb)

    config.validate()
    return config


def _default_output_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "lib" / "ocio" / "sandwich-v01" / "config.ocio"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the sandwich-v01 OCIO config from the ACEScg base."
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE_URI,
        help="OCIO source URI or path (default: ACEScg built-in config).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_default_output_path(),
        help="Output config.ocio path.",
    )
    parser.add_argument(
        "--force-exit",
        action="store_true",
        help="Call os._exit(0) after writing to avoid hython shutdown crashes.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    ocio = _load_ocio()

    config = build_config(ocio, args.source)

    output_path: Path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(config.serialize(), encoding="utf-8")

    sys.stdout.write(f"Wrote {output_path}\n")
    if args.force_exit:
        os._exit(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
