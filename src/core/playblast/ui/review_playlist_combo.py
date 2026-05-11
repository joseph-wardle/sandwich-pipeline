"""Shared Qt widget for the 'select recent ShotGrid review playlist' UI
that the Houdini and Maya playblast dialogs all need. Replaces the three
duplicated copies of the same combobox + Refresh button + lazy-load
machinery."""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from Qt import QtCore, QtWidgets

from core.playblast.shotgrid.playlists import (
    PlayblastReviewPlaylistOption,
    list_recent_review_playlists,
)

log = logging.getLogger(__name__)


class ReviewPlaylistCombo(QtWidgets.QWidget):
    """Combobox + Refresh button for picking a recent ShotGrid review playlist.

    Lazy-loads on first call to `ensure_loaded_lazily()`. `force_refresh()`
    re-fetches ignoring the lazy guard. Emits `selection_changed` whenever
    the user picks a different playlist or a refresh updates the list, so
    the host dialog can re-validate its export state.

    The widget does not depend on any DCC module; it talks to ShotGrid
    only through the injectable `playlist_loader` callable. Pass a custom
    loader (e.g. one that closes over a specific `ShotGrid` connection) if
    the host dialog already holds a connection to reuse.
    """

    selection_changed = QtCore.Signal()

    _combo: QtWidgets.QComboBox
    _lazy_load_attempted: bool
    _load_error: str | None
    _log_context: str
    _playlist_loader: Callable[..., Iterable[PlayblastReviewPlaylistOption]]
    _recent_limit: int
    _refresh_button: QtWidgets.QPushButton

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        recent_limit: int = 10,
        playlist_loader: Callable[..., Iterable[PlayblastReviewPlaylistOption]]
        | None = None,
        log_context: str = "<unknown>",
    ) -> None:
        super().__init__(parent)
        self._recent_limit = recent_limit
        self._playlist_loader = playlist_loader or list_recent_review_playlists
        self._log_context = log_context
        self._lazy_load_attempted = False
        self._load_error = None

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QtWidgets.QLabel("Review"))

        self._combo = QtWidgets.QComboBox(self)
        self._combo.setToolTip(
            "Select the ShotGrid review playlist to link this Version to."
        )
        self._combo.currentIndexChanged.connect(self._on_combo_index_changed)
        layout.addWidget(self._combo)

        self._refresh_button = QtWidgets.QPushButton("Refresh")
        self._refresh_button.setToolTip(
            "Reload the recent ShotGrid review playlist options."
        )
        self._refresh_button.clicked.connect(self._on_refresh_clicked)
        layout.addWidget(self._refresh_button)

        self._set_placeholder("No reviews loaded yet.")

    @property
    def selected_playlist_id(self) -> int | None:
        selected = self._combo.currentData()
        if isinstance(selected, int) and selected > 0:
            return selected
        return None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def set_combo_enabled(self, enabled: bool) -> None:
        """Toggle the combo + refresh button enable state. Used by host
        dialogs that hide the row entirely when review upload is off."""
        self._combo.setEnabled(enabled)
        self._refresh_button.setEnabled(enabled)

    def ensure_loaded_lazily(self) -> None:
        if self._lazy_load_attempted:
            return
        self._load(force_refresh=False)

    def force_refresh(self) -> None:
        self._load(force_refresh=True)

    def _on_refresh_clicked(self) -> None:
        self.force_refresh()
        self.selection_changed.emit()

    def _on_combo_index_changed(self, *_args: Any) -> None:
        self.selection_changed.emit()

    def _load(self, *, force_refresh: bool) -> None:
        if self._lazy_load_attempted and not force_refresh:
            return
        self._lazy_load_attempted = True
        previous_playlist_id = self.selected_playlist_id

        try:
            review_options = list(self._playlist_loader(limit=self._recent_limit))
        except Exception as exc:
            self._load_error = str(exc).strip() or type(exc).__name__
            log.exception(
                "Could not load ShotGrid review playlists for '%s'",
                self._log_context,
            )
            self._set_placeholder("Could not load reviews. Click Refresh.")
            return

        self._load_error = None
        self._populate(review_options, previous_playlist_id)

    def _populate(
        self,
        review_options: list[PlayblastReviewPlaylistOption],
        previous_playlist_id: int | None,
    ) -> None:
        previous_signal_state = self._combo.blockSignals(True)
        try:
            self._combo.clear()
            if not review_options:
                self._combo.addItem("No recent reviews found.", None)
                self._combo.setCurrentIndex(0)
                return

            selected_index = 0
            for index, option in enumerate(review_options):
                label = f"{option.display_name} (#{option.playlist_id})"
                self._combo.addItem(label, option.playlist_id)
                if (
                    previous_playlist_id is not None
                    and option.playlist_id == previous_playlist_id
                ):
                    selected_index = index

            self._combo.setCurrentIndex(selected_index)
        finally:
            self._combo.blockSignals(previous_signal_state)

    def _set_placeholder(self, label: str) -> None:
        previous_signal_state = self._combo.blockSignals(True)
        try:
            self._combo.clear()
            self._combo.addItem(label, None)
            self._combo.setCurrentIndex(0)
        finally:
            self._combo.blockSignals(previous_signal_state)


__all__ = ["ReviewPlaylistCombo"]
