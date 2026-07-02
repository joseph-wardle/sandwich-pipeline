"""Bake the OpenDRT and ACES 2.0 display LUTs referenced by build.py.

Neither transform is expressible in OCIO 2.3 (what we run), so each is sampled
once here into a 3D LUT and committed under ``luts/``. This is an offline recipe.

Requirements (a dev machine, not the render farm):
  * PyOpenColorIO >= 2.4  (ACES 2.0 built-in configs)  — ``pip install --user opencolorio``
  * numpy
  * a C++ compiler (g++/clang++)  — compiles the reference OpenDRT DCTL
  * network access                — fetches the pinned OpenDRT DCTL (GPLv3, not vendored)

Run:  python src/pipe/core/color/bake.py
Then: hython src/pipe/core/color/build.py   (regenerates config.ocio around the LUTs)

Each LUT maps ACEScct-encoded input -> display-linear Rec.709. build.py supplies
the ACEScct shaper (a native ColorSpaceTransform) and the display encode, so the
committed files are plain [0,1]-domain 3D LUTs that load on any OCIO.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import numpy as np
import PyOpenColorIO as ocio

# LUT filenames — must match build.py's OPENDRT_LUT / ACES2_LUT / LUTS_DIRNAME.
LUTS_DIRNAME = "luts"
OPENDRT_LUT = "opendrt_standard.cube"
ACES2_LUT = "aces2_sdr_rec709.cube"

LUT_SIZE = 65

# ACES 1.3 CG base — supplies the ACEScct shaper decode
BASE_URI = "ocio://cg-config-v1.0.0_aces-v1.3_ocio-v2.1"
SHAPER_SPACE = "ACEScct"
ACESCG_SPACE = "ACES - ACEScg"

# ACES 2.0 CG config
ACES2_URI = "ocio://cg-config-v4.0.0_aces-v2.0_ocio-v2.5"
ACES2_DISPLAY = "sRGB - Display"
ACES2_VIEW = "ACES 2.0 - SDR 100 nits (Rec.709)"

# Reference OpenDRT. Standard preset, ACEScg in, Lp=100 (see bake_opendrt.cpp).
OPENDRT_VERSION = "v1.1.0"
OPENDRT_DCTL_URL = (
    "https://raw.githubusercontent.com/jedypod/open-display-transform/"
    f"{OPENDRT_VERSION}/display-transforms/opendrt/OpenDRT.dctl"
)
OPENDRT_DCTL_SHA256 = "75f73562c4b93ea8e9827939a34b4232f169e73816a10261bfe208863b152026"

_HERE = Path(__file__).resolve().parent


def _srgb_decode(code: np.ndarray) -> np.ndarray:
    """Piecewise sRGB EOTF: code values -> display-linear."""
    code = np.clip(code, 0.0, 1.0)
    return np.where(code <= 0.04045, code / 12.92, ((code + 0.055) / 1.055) ** 2.4)


def _acescct_grid() -> tuple[np.ndarray, np.ndarray]:
    """Return (ACEScct grid nodes, decoded ACEScg linear) in .cube order (R fastest)."""
    axis = np.linspace(0.0, 1.0, LUT_SIZE, dtype=np.float32)
    bb, gg, rr = np.meshgrid(axis, axis, axis, indexing="ij")
    cct = (
        np.stack([rr.ravel(), gg.ravel(), bb.ravel()], axis=1).astype(np.float32).copy()
    )
    decode = (
        ocio.Config.CreateFromFile(BASE_URI)
        .getProcessor(SHAPER_SPACE, ACESCG_SPACE)
        .getDefaultCPUProcessor()
    )
    cg = cct.copy()
    decode.applyRGB(cg)
    return cct, cg


def _fetch_opendrt_dctl(dest: Path) -> None:
    """Download + checksum the pinned reference DCTL (writes dest)."""
    data = urllib.request.urlopen(OPENDRT_DCTL_URL, timeout=60).read()
    got = hashlib.sha256(data).hexdigest()
    if got != OPENDRT_DCTL_SHA256:
        raise RuntimeError(
            f"OpenDRT {OPENDRT_VERSION} DCTL checksum mismatch:\n"
            f"  expected {OPENDRT_DCTL_SHA256}\n  got      {got}\n"
            f"  url {OPENDRT_DCTL_URL}"
        )
    dest.write_bytes(data)


def _sample_opendrt(cg: np.ndarray, build_dir: Path) -> np.ndarray:
    """Compile the reference DCTL and evaluate it -> display-linear Rec.709."""
    dctl = build_dir / "OpenDRT.dctl"
    _fetch_opendrt_dctl(dctl)
    binary = build_dir / "bake_opendrt"
    subprocess.run(
        [
            "c++",
            "-O2",
            f'-DOPENDRT_DCTL="{dctl}"',
            "-o",
            str(binary),
            str(_HERE / "bake_opendrt.cpp"),
        ],
        check=True,
    )
    proc = subprocess.run(
        [str(binary)],
        input=cg.astype(np.float32).tobytes(),
        capture_output=True,
        check=True,
    )
    srgb22 = np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 3)
    return np.clip(srgb22, 0.0, 1.0) ** 2.2  # invert the 2.2-power encode -> linear


def _sample_aces2(cg: np.ndarray) -> np.ndarray:
    """Evaluate the ACES 2.0 SDR Rec.709 view -> display-linear Rec.709."""
    proc = (
        ocio.Config.CreateFromFile(ACES2_URI)
        .getProcessor(
            ACESCG_SPACE, ACES2_DISPLAY, ACES2_VIEW, ocio.TRANSFORM_DIR_FORWARD
        )
        .getDefaultCPUProcessor()
    )
    code = cg.copy()
    proc.applyRGB(code)
    return _srgb_decode(code)


def _write_cube(path: Path, table: np.ndarray) -> None:
    lines = [
        f"# {path.name}: ACEScct-domain 3D LUT -> display-linear Rec.709",
        f"LUT_3D_SIZE {LUT_SIZE}",
    ]
    lines += [f"{r:.7f} {g:.7f} {b:.7f}" for r, g, b in table]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    out_dir = _HERE / LUTS_DIRNAME
    out_dir.mkdir(exist_ok=True)
    _cct, cg = _acescct_grid()

    with tempfile.TemporaryDirectory() as tmp:
        opendrt = _sample_opendrt(cg, Path(tmp))
    _write_cube(out_dir / OPENDRT_LUT, opendrt)
    sys.stdout.write(f"Wrote {out_dir / OPENDRT_LUT}\n")

    aces2 = _sample_aces2(cg)
    _write_cube(out_dir / ACES2_LUT, aces2)
    sys.stdout.write(f"Wrote {out_dir / ACES2_LUT}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
