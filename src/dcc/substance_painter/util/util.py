"""Shared utilities for Substance Painter integration.

Simple, lightweight helpers used across the sp module.
"""

from __future__ import annotations

import substance_painter as sp
from Qt import QtCore
from core.util.util import get_documentation_path

# Documentation page reference
PIPE_SP_DOCS_PAGE = "Asset-Pipeline#substance-painter"
"""Wiki page slug for Substance Painter pipeline documentation."""


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


def docs_link_html() -> str:
    """Return an HTML anchor tag linking to the Substance Painter docs page."""
    url = get_documentation_path(PIPE_SP_DOCS_PAGE)
    if "://" not in url:
        url = QtCore.QUrl.fromLocalFile(url).toString()
    return f'<a href="{url}">the documentation</a>'
