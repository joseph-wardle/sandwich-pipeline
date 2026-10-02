"""Substance Painter documentation links surfaced inside SP UI."""

from __future__ import annotations

from Qt import QtCore, QtWidgets

from pipe.core.util.paths import get_documentation_path

PIPE_SP_DOCS_PAGE = "Asset-Pipeline#substance-painter"
"""Wiki page slug for Substance Painter pipeline documentation."""

LOG_HINT = "Check Painter's Log (Window → Views → Log) for details."
"""Painter shows the pipeline's warnings and tracebacks there, not in a console."""


def docs_link_html() -> str:
    """Return an HTML anchor tag linking to the Substance Painter docs page."""
    url = get_documentation_path(PIPE_SP_DOCS_PAGE)
    if "://" not in url:
        url = QtCore.QUrl.fromLocalFile(url).toString()
    return f'<a href="{url}">the documentation</a>'


def docs_footer(html: str) -> QtWidgets.QLabel:
    """Return a grey footer label showing *html*; its links open in the browser."""
    footer = QtWidgets.QLabel(html)
    footer.setWordWrap(True)
    footer.setTextFormat(QtCore.Qt.RichText)
    footer.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
    footer.setOpenExternalLinks(True)
    footer.setStyleSheet("color: #8a8a8a;")
    return footer
