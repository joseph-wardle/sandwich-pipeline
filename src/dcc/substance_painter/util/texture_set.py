"""TextureSet helpers for Substance Painter."""

from __future__ import annotations

import substance_painter as sp


def texture_set_name(tex_set: sp.textureset.TextureSet) -> str:
    """Return the display name of a texture set.

    Handles compatibility across Substance Painter API versions where ``name``
    may be a callable method, a property string, or unavailable.
    """
    name_attr = getattr(tex_set, "name", None)
    if callable(name_attr):
        return name_attr()
    if isinstance(name_attr, str):
        return name_attr
    return str(tex_set)
