"""JSONL → Postgres ingester. Runs on the telemetry host.

The ingester tails the shared spool (`{production_root}/.telemetry/raw/...`),
validates each event's payload shape against the registry in `events.py`, and
inserts a row into the `events` table. Per-spool read offsets are persisted
in `ingester_status` so the ingester resumes cleanly after a restart and the
"ingester lag" Grafana panel can show how far behind real time it is.

Run via systemd on the telemetry host:

    python -m pipe.telemetry.ingester \\
        --spool-root /mnt/show/.telemetry/raw \\
        --db-dsn postgresql://sandwich-telemetry@localhost/sandwich_telemetry

Environment variable equivalents (the systemd unit uses these):

    PIPE_INGESTER_SPOOL_ROOT
    PIPE_INGESTER_DB_DSN
    PIPE_INGESTER_INTERVAL    seconds between scan passes (default 5)

The ingester lives next to `events.py` so the validation it performs on the
read side cannot drift from the contract on the write side. Both halves of
the system import the same `EVENT_DEFINITIONS`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .events import EVENTS_BY_TYPE, EventDefinition

_LOG = logging.getLogger(__name__)

_FILE_PATTERN = "telemetry-*.jsonl"


@dataclass
class _SpoolPosition:
    """Where the ingester last left off on one spool directory."""

    last_jsonl_file: str | None
    last_byte_offset: int


class IngesterRunner:
    """Periodic scanner that pulls JSONL events into Postgres.

    One `scan_once()` pass:

    1. List subdirectories under `spool_root` — each is a per-host/per-user spool.
    2. For each spool, read its `ingester_status` row to find the offset.
    3. Open the most recent file at the saved offset; read lines until EOF.
    4. For each line, parse JSON, validate against the registry, insert one row.
    5. Write the new offset back to `ingester_status`.

    JSONL files are append-only and rotate by size. When a newer file appears
    for the same spool, the ingester finishes draining the older file (up to
    its final size) and then moves on. Files past the writer's retention
    window get deleted; the offset row is keyed by spool dir, not file name,
    so rotation is safe.
    """

    def __init__(
        self,
        *,
        spool_root: Path,
        db_dsn: str,
        interval_seconds: float,
    ) -> None:
        self._spool_root = spool_root
        self._db_dsn = db_dsn
        self._interval_seconds = interval_seconds
        self._stopped = False

    def stop(self) -> None:
        """Request a graceful shutdown after the current scan pass."""

        self._stopped = True

    def run_forever(self) -> None:
        """Scan in a loop, sleeping `interval_seconds` between passes."""

        _LOG.info(
            "Starting telemetry ingester: spool_root=%s interval=%.1fs",
            self._spool_root,
            self._interval_seconds,
        )
        while not self._stopped:
            self.scan_once()
            if self._stopped:
                break
            time.sleep(self._interval_seconds)

    def scan_once(self) -> None:
        """Run one ingestion pass across all spools under `spool_root`."""

        with psycopg.connect(self._db_dsn) as conn:
            for spool_dir in self._discover_spool_dirs():
                self._ingest_spool(conn, spool_dir)

    def _discover_spool_dirs(self) -> Iterator[Path]:
        """Yield each host/user spool directory under `spool_root`.

        Layout: `<spool_root>/<hostname>/<user>/`. Other directories are
        skipped. Missing root logs once and exits the iterator.
        """

        if not self._spool_root.is_dir():
            _LOG.warning(
                "Spool root %s is not a directory; nothing to ingest.",
                self._spool_root,
            )
            return

        for host_dir in sorted(self._spool_root.iterdir()):
            if not host_dir.is_dir():
                continue
            for user_dir in sorted(host_dir.iterdir()):
                if user_dir.is_dir():
                    yield user_dir

    def _ingest_spool(self, conn: psycopg.Connection[Any], spool_dir: Path) -> None:
        spool_key = str(spool_dir)
        position = self._load_position(conn, spool_key)
        target_file = self._select_target_file(spool_dir, position)
        if target_file is None:
            return

        offset = (
            position.last_byte_offset
            if position.last_jsonl_file == target_file.name
            else 0
        )

        new_offset, inserted, rejected, latest_event_at = self._read_and_insert(
            conn, target_file, offset
        )
        self._save_position(
            conn,
            spool_key,
            file_name=target_file.name,
            byte_offset=new_offset,
            last_event_at=latest_event_at,
            inserted=inserted,
            rejected=rejected,
        )
        if inserted or rejected:
            _LOG.info(
                "Ingested %d events (%d rejected) from %s",
                inserted,
                rejected,
                target_file,
            )

    def _select_target_file(
        self, spool_dir: Path, position: _SpoolPosition
    ) -> Path | None:
        """Pick the next file to read for this spool.

        Prefer the file we were already reading. If a newer file exists, jump
        to the newest one once the old file has been fully drained.
        """

        files = sorted(spool_dir.glob(_FILE_PATTERN))
        if not files:
            return None
        if position.last_jsonl_file is not None:
            for candidate in files:
                if candidate.name == position.last_jsonl_file:
                    if candidate is files[-1]:
                        return candidate
                    if candidate.stat().st_size > position.last_byte_offset:
                        return candidate
                    return files[-1]
        return files[-1]

    def _read_and_insert(
        self,
        conn: psycopg.Connection[Any],
        path: Path,
        start_offset: int,
    ) -> tuple[int, int, int, datetime | None]:
        inserted = 0
        rejected = 0
        latest_event_at: datetime | None = None

        with path.open("rb") as handle:
            handle.seek(start_offset)
            with conn.cursor() as cursor:
                for line in handle:
                    if not line.endswith(b"\n"):
                        # Partial line — writer hasn't finished writing it yet.
                        # Stop here so we resume from this position next pass.
                        break

                    decoded = line.decode("utf-8", errors="replace").rstrip("\n")
                    occurred_at = self._insert_one(cursor, decoded)
                    if occurred_at is None:
                        rejected += 1
                    else:
                        inserted += 1
                        if latest_event_at is None or occurred_at > latest_event_at:
                            latest_event_at = occurred_at
            new_offset = handle.tell()

        conn.commit()
        return new_offset, inserted, rejected, latest_event_at

    def _insert_one(
        self, cursor: psycopg.Cursor[Any], jsonl_line: str
    ) -> datetime | None:
        """Insert one JSONL event row. Returns its occurred_at, or None on reject."""

        try:
            event = json.loads(jsonl_line)
        except json.JSONDecodeError as exc:
            _LOG.warning("Skipping malformed JSON line: %s", exc)
            return None

        if not isinstance(event, dict):
            _LOG.warning("Skipping non-object JSON line: %r", event)
            return None

        definition = EVENTS_BY_TYPE.get(event.get("event_type", ""))
        if definition is None:
            _LOG.warning("Skipping unknown event_type=%r", event.get("event_type"))
            return None

        if not self._payload_is_valid(definition, event):
            return None

        occurred_at = self._parse_timestamp(event.get("occurred_at"))
        if occurred_at is None:
            _LOG.warning(
                "Skipping event_type=%s with unparseable occurred_at=%r",
                event["event_type"],
                event.get("occurred_at"),
            )
            return None

        scope = event.get("scope") or {}
        cursor.execute(
            """
            INSERT INTO events (
                event_id, occurred_at, event_type, status,
                dcc, host_user, hostname, action_id,
                scope_show, scope_sequence, scope_shot, scope_asset, scope_department,
                duration_ms, error_code, error_message, payload
            )
            VALUES (
                %(event_id)s, %(occurred_at)s, %(event_type)s, %(status)s,
                %(dcc)s, %(host_user)s, %(hostname)s, %(action_id)s,
                %(scope_show)s, %(scope_sequence)s, %(scope_shot)s, %(scope_asset)s, %(scope_department)s,
                %(duration_ms)s, %(error_code)s, %(error_message)s, %(payload)s
            )
            ON CONFLICT (event_id) DO NOTHING
            """,
            {
                "event_id": event.get("event_id"),
                "occurred_at": occurred_at,
                "event_type": event["event_type"],
                "status": event.get("status"),
                "dcc": event.get("dcc"),
                "host_user": event.get("host_user"),
                "hostname": event.get("hostname"),
                "action_id": event.get("action_id"),
                "scope_show": scope.get("show"),
                "scope_sequence": scope.get("sequence"),
                "scope_shot": scope.get("shot"),
                "scope_asset": scope.get("asset"),
                "scope_department": scope.get("department"),
                "duration_ms": event.get("duration_ms"),
                "error_code": event.get("error_code"),
                "error_message": event.get("error_message"),
                "payload": Jsonb(event.get("payload", {})),
            },
        )
        return occurred_at

    @staticmethod
    def _payload_is_valid(definition: EventDefinition, event: dict[str, Any]) -> bool:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            _LOG.warning(
                "Skipping event_type=%s with non-object payload",
                definition.event_type,
            )
            return False
        if event.get("status") not in definition.statuses:
            _LOG.warning(
                "Skipping event_type=%s with disallowed status=%r",
                definition.event_type,
                event.get("status"),
            )
            return False
        missing = [
            field
            for field in definition.required_payload_fields
            if field not in payload
        ]
        if missing:
            _LOG.warning(
                "Skipping event_type=%s missing payload fields=%s",
                definition.event_type,
                missing,
            )
            return False
        return True

    @staticmethod
    def _parse_timestamp(raw: Any) -> datetime | None:
        if not isinstance(raw, str):
            return None
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    @staticmethod
    def _load_position(conn: psycopg.Connection[Any], spool_key: str) -> _SpoolPosition:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT last_jsonl_file, last_byte_offset "
                "FROM ingester_status WHERE spool_path = %s",
                (spool_key,),
            )
            row = cursor.fetchone()
        if row is None:
            return _SpoolPosition(last_jsonl_file=None, last_byte_offset=0)
        return _SpoolPosition(last_jsonl_file=row[0], last_byte_offset=row[1])

    @staticmethod
    def _save_position(
        conn: psycopg.Connection[Any],
        spool_key: str,
        *,
        file_name: str,
        byte_offset: int,
        last_event_at: datetime | None,
        inserted: int,
        rejected: int,
    ) -> None:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ingester_status (
                    spool_path, last_jsonl_file, last_byte_offset,
                    last_event_at, last_read_at, events_inserted, events_rejected
                )
                VALUES (%s, %s, %s, %s, now(), %s, %s)
                ON CONFLICT (spool_path) DO UPDATE SET
                    last_jsonl_file = EXCLUDED.last_jsonl_file,
                    last_byte_offset = EXCLUDED.last_byte_offset,
                    last_event_at = COALESCE(EXCLUDED.last_event_at, ingester_status.last_event_at),
                    last_read_at = now(),
                    events_inserted = ingester_status.events_inserted + EXCLUDED.events_inserted,
                    events_rejected = ingester_status.events_rejected + EXCLUDED.events_rejected
                """,
                (
                    spool_key,
                    file_name,
                    byte_offset,
                    _coerce_naive_to_utc(last_event_at) if last_event_at else None,
                    inserted,
                    rejected,
                ),
            )
        conn.commit()


def _coerce_naive_to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# ---------------------------------------------------------------------------
# CLI entrypoint — invoked via `python -m pipe.telemetry.ingester` from the
# systemd unit. The CLI is a thin wrapper over IngesterRunner; tests exercise
# the runner directly without going through this layer.
# ---------------------------------------------------------------------------


def _build_runner(args: argparse.Namespace) -> IngesterRunner:
    spool_root = Path(args.spool_root).expanduser().resolve()
    if not spool_root.exists():
        raise SystemExit(
            f"Spool root does not exist: {spool_root}. "
            "Set --spool-root or PIPE_INGESTER_SPOOL_ROOT."
        )
    return IngesterRunner(
        spool_root=spool_root,
        db_dsn=args.db_dsn,
        interval_seconds=args.interval,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pipe.telemetry.ingester",
        description="Tail the shared telemetry JSONL spool into Postgres.",
    )
    parser.add_argument(
        "--spool-root",
        default=os.environ.get("PIPE_INGESTER_SPOOL_ROOT"),
        required=os.environ.get("PIPE_INGESTER_SPOOL_ROOT") is None,
        help="Directory containing per-host/per-user JSONL spool subdirs.",
    )
    parser.add_argument(
        "--db-dsn",
        default=os.environ.get("PIPE_INGESTER_DB_DSN"),
        required=os.environ.get("PIPE_INGESTER_DB_DSN") is None,
        help="Postgres DSN, e.g. postgresql://user@host/dbname.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("PIPE_INGESTER_INTERVAL", "5")),
        help="Seconds between scan passes (default: 5).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Scan once and exit. Useful for batch backfill or testing.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    runner = _build_runner(args)

    signal.signal(signal.SIGTERM, lambda *_: runner.stop())
    signal.signal(signal.SIGINT, lambda *_: runner.stop())

    if args.once:
        runner.scan_once()
        return 0
    runner.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["IngesterRunner", "main"]
