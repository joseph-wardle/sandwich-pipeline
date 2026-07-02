# Display LUTs (generated — do not hand-edit)

Two 3D LUTs grafted onto `sRGB - Display` by `build.py`, because OCIO 2.3 (the
DCC fleet) can evaluate neither transform natively:

| File                     | Transform                                  | Source                                            |
| ------------------------ | ------------------------------------------ | ------------------------------------------------- |
| `opendrt_standard.cube`  | OpenDRT v1.1.0, Standard preset (show look) | reference `OpenDRT.dctl`, compiled on CPU         |
| `aces2_sdr_rec709.cube`  | ACES 2.0 SDR 100 nits (Rec.709)             | OCIO 2.5 `cg-config-v4.0.0_aces-v2.0`             |

Both map an **ACEScct-encoded** input to **display-linear Rec.709** (65³,
tetrahedral). The shaper, B&W saturation matrix, and display encode are authored
natively in `build.py`, so these files are plain [0,1]-domain LUTs.

Regenerate (offline; needs PyOpenColorIO ≥ 2.4 + a C++ compiler + network):

    python src/pipe/core/color/bake.py

See `../bake.py`, `../bake_opendrt.cpp`, and `context/color.md`.
