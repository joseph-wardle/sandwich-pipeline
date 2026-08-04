"""The viewer's one review-playlist lookup, shared by every ShotGrid row."""

from __future__ import annotations

import logging

from Qt.QtCore import QObject, QRunnable, QThreadPool, Signal

from pipe.core.playblast.review.playlists import (
    PlayblastReviewPlaylistOption,
    list_recent_review_playlists,
)

log = logging.getLogger(__name__)

_PLAYLIST_LIMIT = 10


class _FetchSignals(QObject):
    finished = Signal(object)  # tuple[PlayblastReviewPlaylistOption, ...]
    failed = Signal(str)


class _FetchJob(QRunnable):
    signals: _FetchSignals

    def __init__(self) -> None:
        super().__init__()
        # The source holds a reference until the job reports back, so Python
        # keeps ownership of the C++ runnable.
        self.setAutoDelete(False)
        self.signals = _FetchSignals()

    def run(self) -> None:
        try:
            self.signals.finished.emit(
                list_recent_review_playlists(limit=_PLAYLIST_LIMIT)
            )
        except Exception as exc:
            log.exception("Could not load ShotGrid review playlists")
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


class ReviewPlaylists(QObject):
    """Recent review playlists, fetched off the GUI thread at most once.

    Every clip in a viewer offers the same playlists, so one window-wide fetch
    replaces one blocking ShotGrid round trip per ShotGrid row.
    """

    changed = Signal()

    _pool: QThreadPool
    _options: tuple[PlayblastReviewPlaylistOption, ...]
    _error: str | None
    _loading: bool
    _settled: bool
    _job: _FetchJob | None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Its own pool, so a hung ShotGrid read can never occupy the thread a
        # Confirm delivery needs.
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._options = ()
        self._error = None
        self._loading = False
        self._settled = False
        self._job = None

    @property
    def options(self) -> tuple[PlayblastReviewPlaylistOption, ...]:
        return self._options

    @property
    def error(self) -> str | None:
        """Why the last fetch failed, or None if it succeeded."""
        return self._error

    @property
    def settled(self) -> bool:
        """Whether a fetch has finished. Until it has, `options` says nothing."""
        return self._settled

    def ensure_loaded(self) -> None:
        if self._loading or self._settled:
            return
        self.reload()

    def reload(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._settled = False
        self._error = None
        self.changed.emit()

        job = _FetchJob()
        job.signals.finished.connect(self._on_finished)
        job.signals.failed.connect(self._on_failed)
        self._job = job
        self._pool.start(job)

    def _on_finished(self, options: tuple[PlayblastReviewPlaylistOption, ...]) -> None:
        self._settle(options, error=None)

    def _on_failed(self, reason: str) -> None:
        self._settle((), error=reason)

    def _settle(
        self, options: tuple[PlayblastReviewPlaylistOption, ...], *, error: str | None
    ) -> None:
        self._job = None
        self._loading = False
        self._settled = True
        self._options = options
        self._error = error
        self.changed.emit()


__all__ = ["ReviewPlaylists"]
