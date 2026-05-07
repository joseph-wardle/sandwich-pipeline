"""JSONL spool writer for telemetry events.

Events are dropped onto an in-process queue; a daemon thread drains the queue
and appends serialized JSON lines to files in the configured spool directory.
The writer is fail-open: if the disk write fails, the event is dropped and a
rate-limited WARNING is logged to stderr. Pipeline workflows never block on
telemetry.

File layout: one or more files per process, named
`telemetry-<UTC timestamp>-<pid>-<seq>.jsonl`. Files rotate when they exceed
`rotate_mb`. By default the JSONL spool is kept forever — the spool is the
canonical record of every emitted event, and Postgres is derived state.
Setting `retention_days` to a positive integer opts into time-based
pruning if disk pressure ever becomes a concern.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Mapping

from .config import TelemetryConfig, load_config

_LOG = logging.getLogger(__name__)

_DROP_LOG_EVERY_N: Final[int] = 100
_RETENTION_SWEEP_INTERVAL_SECONDS: Final[float] = 300.0
_CLOSE_TIMEOUT_SECONDS: Final[float] = 5.0
_FILE_SUFFIX: Final[str] = ".jsonl"
_FILENAME_PREFIX: Final[str] = "telemetry"


class NullSpoolWriter:
    """No-op writer used when telemetry is disabled."""

    def write_event(self, event: Mapping[str, Any]) -> None:
        del event

    def flush(self) -> None: ...

    def close(self) -> None: ...


class AsyncJsonlSpoolWriter:
    """Queue-backed JSONL writer with size-based rotation and time-based retention."""

    def __init__(self, config: TelemetryConfig) -> None:
        self._spool_dir = config.spool_dir
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=config.queue_max)
        self._flush_seconds = config.flush_seconds
        self._rotate_bytes = config.rotate_mb * 1024 * 1024
        self._retention_seconds = config.retention_days * 86400

        self._stop_event = threading.Event()
        self._handle: Any = None
        self._active_path: Path | None = None
        self._active_size_bytes = 0
        self._file_seq = 0
        self._last_retention_sweep = 0.0
        self._dropped_total = 0
        self._dropped_lock = threading.Lock()

        self._worker = threading.Thread(
            target=self._worker_loop,
            name="pipe-telemetry-spool-writer",
            daemon=True,
        )
        self._worker.start()

    def write_event(self, event: Mapping[str, Any]) -> None:
        try:
            self._queue.put_nowait(dict(event))
        except queue.Full:
            self._record_drop(reason="queue_full")

    def flush(self) -> None:
        deadline = time.monotonic() + _CLOSE_TIMEOUT_SECONDS
        while self._queue.unfinished_tasks and self._worker.is_alive():
            if time.monotonic() > deadline:
                _LOG.warning(
                    "Telemetry flush timed out with %d events still pending.",
                    self._queue.unfinished_tasks,
                )
                return
            time.sleep(0.01)
        if self._handle is not None:
            self._handle.flush()

    def close(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        self.flush()
        self._worker.join(timeout=_CLOSE_TIMEOUT_SECONDS)
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def _record_drop(self, *, reason: str) -> None:
        with self._dropped_lock:
            self._dropped_total += 1
            count = self._dropped_total
        if count == 1 or count % _DROP_LOG_EVERY_N == 0:
            _LOG.warning(
                "Telemetry dropped %d event(s) (most recent reason: %s).",
                count,
                reason,
            )

    def _worker_loop(self) -> None:
        try:
            self._spool_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _LOG.warning(
                "Telemetry spool dir %s is unavailable: %s. Events from this "
                "process will be dropped until the directory is writable.",
                self._spool_dir,
                exc,
            )
            return

        self._sweep_retention(force=True)

        while not (self._stop_event.is_set() and self._queue.empty()):
            try:
                event = self._queue.get(timeout=self._flush_seconds)
            except queue.Empty:
                self._sweep_retention(force=False)
                continue

            try:
                self._write_one(event)
            finally:
                self._queue.task_done()

        if self._handle is not None:
            self._handle.flush()

    def _write_one(self, event: Mapping[str, Any]) -> None:
        try:
            line = json.dumps(event, sort_keys=True, ensure_ascii=True)
        except (TypeError, ValueError) as exc:
            self._record_drop(reason=f"serialize_failed: {exc}")
            return

        encoded_size = len(line.encode("utf-8")) + 1  # +1 for the newline

        try:
            self._rotate_if_needed(incoming_bytes=encoded_size)
            handle = self._open_active_file()
            handle.write(line)
            handle.write("\n")
            self._active_size_bytes += encoded_size
        except OSError as exc:
            self._record_drop(reason=f"write_failed: {exc}")

    def _open_active_file(self) -> Any:
        if self._handle is not None:
            return self._handle
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._file_seq += 1
        path = self._spool_dir / (
            f"{_FILENAME_PREFIX}-{timestamp}-{os.getpid()}-"
            f"{self._file_seq:04d}{_FILE_SUFFIX}"
        )
        self._handle = path.open("a", encoding="utf-8")
        self._active_path = path
        self._active_size_bytes = 0
        return self._handle

    def _rotate_if_needed(self, *, incoming_bytes: int) -> None:
        if self._handle is None:
            return
        if self._active_size_bytes + incoming_bytes <= self._rotate_bytes:
            return
        self._handle.close()
        self._handle = None
        self._sweep_retention(force=False)

    def _sweep_retention(self, *, force: bool) -> None:
        # `retention_days=0` (the default) means "never sweep" — JSONL files
        # are the canonical record of every emitted event and we keep them
        # indefinitely so Postgres can be rebuilt from scratch if needed.
        if self._retention_seconds <= 0:
            return

        now = time.monotonic()
        if (
            not force
            and (now - self._last_retention_sweep) < _RETENTION_SWEEP_INTERVAL_SECONDS
        ):
            return
        self._last_retention_sweep = now

        cutoff_epoch = time.time() - self._retention_seconds
        try:
            candidates = list(
                self._spool_dir.glob(f"{_FILENAME_PREFIX}-*{_FILE_SUFFIX}")
            )
        except OSError as exc:
            _LOG.debug("Telemetry retention sweep could not list spool dir: %s", exc)
            return

        for candidate in candidates:
            try:
                if candidate.stat().st_mtime < cutoff_epoch:
                    candidate.unlink()
            except OSError:
                continue


_writer_lock = threading.Lock()
_writer: NullSpoolWriter | AsyncJsonlSpoolWriter | None = None


def get_spool_writer() -> NullSpoolWriter | AsyncJsonlSpoolWriter:
    """Return the process-wide writer, creating it on first use.

    A NullSpoolWriter is returned when telemetry is disabled. A failure to
    create the async writer (e.g. unavailable spool directory) also falls back
    to NullSpoolWriter — telemetry must never block the pipeline.
    """

    global _writer
    with _writer_lock:
        if _writer is not None:
            return _writer

        config = load_config()
        if not config.enabled:
            _writer = NullSpoolWriter()
            return _writer

        try:
            _writer = AsyncJsonlSpoolWriter(config)
        except OSError as exc:
            _LOG.warning(
                "Falling back to NullSpoolWriter because the JSONL writer "
                "could not be initialized: %s.",
                exc,
            )
            _writer = NullSpoolWriter()
        return _writer


def configure_spool_writer_for_test(
    writer: NullSpoolWriter | AsyncJsonlSpoolWriter | None,
) -> None:
    """Replace the process writer. Tests only — never call from production code."""

    global _writer
    with _writer_lock:
        if _writer is not None and _writer is not writer:
            _writer.close()
        _writer = writer


__all__ = [
    "NullSpoolWriter",
    "AsyncJsonlSpoolWriter",
    "get_spool_writer",
    "configure_spool_writer_for_test",
]
