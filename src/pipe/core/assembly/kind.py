"""The published root of an assembly is `kind = "assembly"`."""

from __future__ import annotations

from pathlib import Path

from pxr import Sdf

ASSEMBLY_KIND = "assembly"


def mark_published_assembly(entry_layer: Path) -> None:
    """Rewrite the root kind on `entry_layer` and on its payload."""
    layer = Sdf.Layer.FindOrOpen(str(entry_layer))
    root = layer.GetPrimAtPath(f"/{layer.defaultPrim}")
    for payload in root.payloadList.GetAddedOrExplicitItems():
        _set_root_kind(
            Sdf.Layer.FindOrOpen(layer.ComputeAbsolutePath(payload.assetPath))
        )
    _set_root_kind(layer)


def _set_root_kind(layer: Sdf.Layer) -> None:
    layer.GetPrimAtPath(f"/{layer.defaultPrim}").kind = ASSEMBLY_KIND
    layer.Save()
