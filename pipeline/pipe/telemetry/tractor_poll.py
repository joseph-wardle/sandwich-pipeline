"""Tractor farm-pressure poller.

Queries Tractor's HTTP queue endpoint and emits one `tractor.farm.snapshot`
event with the values it can find. Run on a schedule (systemd timer or cron)
to produce the saturation graph.

Run via:

    python -m pipe.telemetry.tractor_poll \\
        --engine-url http://tractor.lab.byu.edu \\
        --once

Tractor's queue-stats JSON shape varies by version. The poller tries a small
set of canonical key paths; missing keys default to 0 with a warning. Extend
`_FIELD_PATHS` if your Tractor version uses different keys.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Final, Sequence

from .emit import emit
from .events import EVENT_TRACTOR_FARM_SNAPSHOT, STATUS_INFO

_LOG = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SECONDS: Final[float] = 300.0
_HTTP_TIMEOUT_SECONDS: Final[float] = 10.0
_QUEUE_STATS_PATH: Final[str] = "/Tractor/queue?q=stats&format=json"

# For each payload field, a list of candidate JSON paths to try in order.
# The first path that resolves to a number wins. Paths are dotted strings,
# read against the parsed JSON response.
_FIELD_PATHS: Final[dict[str, tuple[str, ...]]] = {
    "waiting_jobs": ("jobs.waiting", "waiting_jobs", "queue.waiting"),
    "running_jobs": ("jobs.running", "running_jobs", "queue.running"),
    "busy_slots": ("slots.busy", "busy_slots", "slots.in_use"),
    "total_slots": ("slots.total", "total_slots", "slots.capacity"),
    "active_blades": ("blades.active", "active_blades", "blades.up"),
    "total_blades": ("blades.total", "total_blades", "blades.count"),
}


def fetch_queue_stats(engine_url: str) -> dict[str, Any]:
    """GET the Tractor queue stats endpoint and return the parsed JSON.

    Raises `urllib.error.URLError` (or its subclasses) if the request fails;
    raises `ValueError` if the response is not valid JSON.
    """

    url = engine_url.rstrip("/") + _QUEUE_STATS_PATH
    with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def _read_path(data: Any, dotted: str) -> int | None:
    """Walk `data` along `dotted` (e.g. 'jobs.waiting'); return int or None."""

    cursor: Any = data
    for segment in dotted.split("."):
        if not isinstance(cursor, dict) or segment not in cursor:
            return None
        cursor = cursor[segment]
    if isinstance(cursor, bool):
        return int(cursor)
    if isinstance(cursor, (int, float)):
        return int(cursor)
    return None


def _resolve_field(data: Any, candidates: Sequence[str]) -> int:
    """Return the first numeric value found at any of `candidates`. 0 if none."""

    for path in candidates:
        value = _read_path(data, path)
        if value is not None:
            return value
    return 0


def build_snapshot_payload(engine_url: str, stats: dict[str, Any]) -> dict[str, Any]:
    """Resolve `stats` against the canonical field paths into a payload dict."""

    payload: dict[str, Any] = {"engine_url": engine_url}
    for field, candidates in _FIELD_PATHS.items():
        payload[field] = _resolve_field(stats, candidates)
    return payload


def poll_once(engine_url: str) -> None:
    """One poll: fetch queue stats, emit one `tractor.farm.snapshot` event."""

    try:
        stats = fetch_queue_stats(engine_url)
    except (urllib.error.URLError, ValueError) as exc:
        _LOG.warning("Tractor queue stats fetch failed: %s", exc)
        emit(
            EVENT_TRACTOR_FARM_SNAPSHOT,
            status=STATUS_INFO,
            payload=build_snapshot_payload(engine_url, {}),
        )
        return

    emit(
        EVENT_TRACTOR_FARM_SNAPSHOT,
        status=STATUS_INFO,
        payload=build_snapshot_payload(engine_url, stats),
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pipe.telemetry.tractor_poll",
        description="Poll Tractor and emit one tractor.farm.snapshot event.",
    )
    parser.add_argument(
        "--engine-url",
        required=True,
        help="Tractor engine URL, e.g. http://tractor.lab.byu.edu",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Poll once and exit (default behavior under systemd timer).",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=_DEFAULT_INTERVAL_SECONDS,
        help="Seconds between polls when running in loop mode.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    if args.once:
        poll_once(args.engine_url)
        return 0

    while True:
        poll_once(args.engine_url)
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "fetch_queue_stats",
    "build_snapshot_payload",
    "poll_once",
    "main",
]
