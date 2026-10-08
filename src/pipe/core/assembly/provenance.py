"""Which assembly a child asset was split from, recorded on its source layer."""

from __future__ import annotations

from pathlib import Path

from pxr import Sdf

from pipe.core.asset.paths import production_relative_identifier

ASSEMBLY_KEY = "assembly"


def stamp_assembly(layer: Sdf.Layer, assembly_root: Path) -> None:
    """Record on `layer`, without saving it, the assembly the child was split from."""
    layer.customLayerData = {
        **layer.customLayerData,
        ASSEMBLY_KEY: production_relative_identifier(assembly_root),
    }
