"""Render statistics harvester.

Walks one or more render artifact roots, finds completed-job directories
that have a `telemetry/render-stats.json` summary, and emits one
`render.stats.summary` event per unseen job. Job-id deduplication is held
in a small state file so subsequent runs are idempotent.

Run via:

    python -m pipe.telemetry.render_harvest /mnt/show/render --once

The expected per-job summary file shape is documented in the registry
(`required_payload_fields` for `EVENT_RENDER_STATS_SUMMARY`):

    {
        "job_id": "j123456",
        "renderer": "prman",
        "total_frames": 240,
        "failed_frames": 0,
        "frame_time_p50_ms": 12345,
        "frame_time_p90_ms": 67890,
        "memory_peak_gb": 12.5,
        "retry_count_total": 3,
        "queue_wait_ms": 1200000
    }

If a render submission tool isn't yet writing this file, this harvester is
a no-op. Producing the file is part of the canonical-path tooling work
(out of scope for the telemetry refactor).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Final

from .emit import emit
from .events import EVENT_RENDER_STATS_SUMMARY, STATUS_SUCCESS

_LOG = logging.getLogger(__name__)

_SUMMARY_FILENAME: Final[str] = "telemetry/render-stats.json"
_DEFAULT_INTERVAL_SECONDS: Final[float] = 900.0
_DEFAULT_STATE_FILE: Final[Path] = (
    Path.home() / ".cache" / "sandwich-pipeline" / "render-harvest-state.json"
)


def _load_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("Could not read state file %s: %s. Treating as empty.", path, exc)
        return set()
    if not isinstance(data, dict):
        return set()
    seen = data.get("seen_job_ids")
    if not isinstance(seen, list):
        return set()
    return {str(job_id) for job_id in seen}


def _save_state(path: Path, seen_job_ids: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"seen_job_ids": sorted(seen_job_ids)}, indent=2),
        encoding="utf-8",
    )


def _load_summary(summary_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("Could not read render summary %s: %s", summary_path, exc)
        return None
    if not isinstance(data, dict) or "job_id" not in data:
        _LOG.warning("Render summary %s missing job_id; skipping.", summary_path)
        return None
    return data


def harvest(roots: list[Path], state_file: Path) -> int:
    """Scan `roots` for unseen render summaries; emit one event per new one.

    Returns the number of events emitted.
    """

    seen_job_ids = _load_state(state_file)
    emitted = 0

    for root in roots:
        if not root.is_dir():
            _LOG.warning("Render artifact root %s is not a directory.", root)
            continue
        for summary_path in root.rglob(_SUMMARY_FILENAME):
            summary = _load_summary(summary_path)
            if summary is None:
                continue
            job_id = str(summary["job_id"])
            if job_id in seen_job_ids:
                continue

            emit(
                EVENT_RENDER_STATS_SUMMARY,
                status=STATUS_SUCCESS,
                payload=summary,
            )
            seen_job_ids.add(job_id)
            emitted += 1

    _save_state(state_file, seen_job_ids)
    return emitted


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pipe.telemetry.render_harvest",
        description="Harvest render-stats summaries into telemetry events.",
    )
    parser.add_argument(
        "roots",
        nargs="+",
        type=Path,
        help="One or more render artifact root directories to scan.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=_DEFAULT_STATE_FILE,
        help="JSON file persisting seen job_ids across runs.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Scan once and exit (default behavior under systemd timer).",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=_DEFAULT_INTERVAL_SECONDS,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    if args.once:
        emitted = harvest(args.roots, args.state_file)
        _LOG.info("Harvested %d new render summaries.", emitted)
        return 0

    while True:
        emitted = harvest(args.roots, args.state_file)
        _LOG.info("Harvested %d new render summaries.", emitted)
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["harvest", "main"]
