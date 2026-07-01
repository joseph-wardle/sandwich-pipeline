"""Pure-function helpers for editing time samples on a USD layer."""

from __future__ import annotations

from pathlib import Path

from pxr import Sdf


def offset_layer(layer_path: Path, offset: float) -> int:
    """Shift every time sample on `layer_path` by `offset`. Returns the count shifted."""
    if offset == 0:
        return 0

    layer = Sdf.Layer.FindOrOpen(str(layer_path))
    if layer is None:
        raise FileNotFoundError(f"USD layer not found: {layer_path}")

    shifted = 0

    def visit(path: Sdf.Path | str) -> None:
        nonlocal shifted
        spec = layer.GetObjectAtPath(path)
        if not isinstance(spec, Sdf.AttributeSpec):
            return
        samples = layer.ListTimeSamplesForPath(path)
        if not samples:
            return
        # Sdf has no atomic shift; read all, erase, rewrite.
        values = [(t, layer.QueryTimeSample(path, t)) for t in samples]
        for t in samples:
            layer.EraseTimeSample(path, t)
        for t, v in values:
            layer.SetTimeSample(path, t + offset, v)
        shifted += len(values)

    layer.Traverse(Sdf.Path.absoluteRootPath, visit)

    if layer.HasStartTimeCode():
        layer.startTimeCode = layer.startTimeCode + offset
    if layer.HasEndTimeCode():
        layer.endTimeCode = layer.endTimeCode + offset

    layer.Save()
    return shifted
