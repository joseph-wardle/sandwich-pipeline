"""The viewer's one review-playlist lookup, shared by every ShotGrid row."""

from __future__ import annotations

import logging

from Qt.QtCore import QObject, QRunnable, QThreadPool, Signal

from pipe.core.playblast.errors import artist_reason
from pipe.core.playblast.review.playlists import (
    PlayblastReviewPlaylistOption,
    list_review_playlists,
)

log = logging.getLogger(__name__)

_PLAYLIST_LIMIT = 10


class _FetchSignals(QObject):
    finished = Signal(object)  # tuple[PlayblastReviewPlaylistOption, ...]
    failed = Signal(str)


class _FetchJob(QRunnable):
    signals: _FetchSignals
    _search: str

    def __init__(self, search: str) -> None:
        super().__init__()
        # The source holds a reference until the job reports back, so Python
        # keeps ownership of the C++ runnable.
        self.setAutoDelete(False)
        self.signals = _FetchSignals()
        self._search = search

    def run(self) -> None:
        try:
            self.signals.finished.emit(
                list_review_playlists(search=self._search, limit=_PLAYLIST_LIMIT)
            )
        except Exception as exc:
            log.exception("Could not load ShotGrid review playlists")
            self.signals.failed.emit(artist_reason(exc))


class ReviewPlaylists(QObject):
    """Review playlists, fetched off the GUI thread and refetched on demand.

    Every clip in a viewer offers the same playlists, so one window-wide fetch
    replaces one blocking ShotGrid round trip per ShotGrid row. `search` is
    shared for the same reason: an artist filtering to today's dailies wants it
    to hold across the whole batch.
    """

    changed = Signal()

    _pool: QThreadPool
    _options: tuple[PlayblastReviewPlaylistOption, ...]
    _search: str
    _error: str | None
    _settled: bool
    _jobs: list[_FetchJob]

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Its own pool, so a hung ShotGrid read can never occupy the thread a
        # Confirm delivery needs.
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._options = ()
        self._search = ""
        self._error = None
        self._settled = False
        self._jobs = []

    @property
    def options(self) -> tuple[PlayblastReviewPlaylistOption, ...]:
        return self._options

    @property
    def search(self) -> str:
        """The code substring `options` is filtered by, or "" for unfiltered."""
        return self._search

    @property
    def error(self) -> str | None:
        """Why the last fetch failed, or None if it succeeded."""
        return self._error

    @property
    def settled(self) -> bool:
        """Whether a fetch has finished. Until it has, `options` says nothing."""
        return self._settled

    def ensure_loaded(self) -> None:
        """Fetch for the first row that needs the list."""
        if self._jobs or self._settled:
            return
        self._fetch()

    def load(self, search: str) -> None:
        """Refetch under a new code filter. Always live."""
        self._search = search.strip()
        self._fetch()

    def _fetch(self) -> None:
        self._settled = False
        self._error = None
        self.changed.emit()

        job = _FetchJob(self._search)
        job.signals.finished.connect(self._on_finished)
        job.signals.failed.connect(self._on_failed)
        self._jobs.append(job)
        self._pool.start(job)

    def _on_finished(self, options: tuple[PlayblastReviewPlaylistOption, ...]) -> None:
        self._settle(options, error=None)

    def _on_failed(self, reason: str) -> None:
        self._settle((), error=reason)

    def _settle(
        self, options: tuple[PlayblastReviewPlaylistOption, ...], *, error: str | None
    ) -> None:
        # The single-threaded pool reports jobs in start order, so a job still
        # queued here carries a newer filter than this result.
        self._jobs.pop(0)
        if self._jobs:
            return
        self._settled = True
        self._options = options
        self._error = error
        self.changed.emit()


__all__ = ["ReviewPlaylists"]
