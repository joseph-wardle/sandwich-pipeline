"""Viewer-venv entry point: `python -m pipe.viewer <preview-spec>`."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from pipe.core.playblast.preview_spec import PreviewSpecError, load_preview_spec
from pipe.viewer.theme import apply_dark_theme
from pipe.viewer.window import ViewerWindow


def main(argv: list[str]) -> int:
    app = QApplication(argv)
    apply_dark_theme(app)

    if len(argv) != 2:
        _fail("Usage: python -m pipe.viewer <preview-spec.json>")
        return 2
    try:
        spec = load_preview_spec(Path(argv[1]))
    except PreviewSpecError as exc:
        _fail(str(exc))
        return 1

    window = ViewerWindow(spec)
    window.show()
    return app.exec()


def _fail(message: str) -> None:
    # The viewer is usually spawned detached from a DCC, so a dialog is the
    # only place an artist will ever see this; stderr is for developers.
    print(f"playblast viewer: {message}", file=sys.stderr)
    QMessageBox.critical(None, "Playblast Viewer", message)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
