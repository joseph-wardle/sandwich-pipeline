"""Storage scanner: classify directory trees into category buckets and emit
`storage.scan.summary` and `storage.scan.bucket` events.

Reads classification rules from `rules/storage_categories.v1.json`. The rule
file maps path-pattern regexes to a category and a retention strategy
(`keep`, `older_than_days:N`, `newer_version_exists`). Anything that doesn't
match falls into the `uncategorized` bucket — which surfaces on the Grafana
dashboard so upstream tools that put files in non-canonical paths can be
fixed.

Run via:

    python -m pipe.telemetry.storage_scan /mnt/show --once

For Phase 2 this is a structural skeleton: it walks the directory tree,
tallies bytes/files per category, and emits one bucket event per category.
The `newer_version_exists` retention strategy requires upstream tools to
write paths in a parseable layout — see the plan's "out of scope" section.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .emit import emit
from .events import (
    EVENT_STORAGE_SCAN_BUCKET,
    EVENT_STORAGE_SCAN_SUMMARY,
    STATUS_INFO,
    STATUS_SUCCESS,
)

_LOG = logging.getLogger(__name__)

_DEFAULT_RULES_PATH: Final[Path] = (
    Path(__file__).parent / "rules" / "storage_categories.v1.json"
)
_UNCATEGORIZED: Final[str] = "uncategorized"


@dataclass(frozen=True)
class _CategoryRule:
    category: str
    matchers: tuple[re.Pattern[str], ...]
    retention: str


def _load_rules(path: Path) -> tuple[list[_CategoryRule], str]:
    """Load (rules, uncategorized_retention) from the rule file."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    rules: list[_CategoryRule] = []
    for entry in raw.get("rules", []):
        category = str(entry["category"])
        patterns = entry.get("match", [])
        compiled = tuple(re.compile(pattern) for pattern in patterns)
        retention = str(entry.get("retention", "keep"))
        rules.append(_CategoryRule(category, compiled, retention))
    uncategorized_retention = str(raw.get("uncategorized", {}).get("retention", "keep"))
    return rules, uncategorized_retention


def _classify(rel_path: str, rules: list[_CategoryRule]) -> str | None:
    """Return the first category whose matcher matches `rel_path`, or None."""

    for rule in rules:
        for pattern in rule.matchers:
            if pattern.search(rel_path):
                return rule.category
    return None


def _retention_for(category: str, rules: list[_CategoryRule], default: str) -> str:
    for rule in rules:
        if rule.category == category:
            return rule.retention
    return default


@dataclass
class _BucketAccumulator:
    category: str
    size_bytes: int = 0
    file_count: int = 0


def _walk_root(
    root: Path,
    rules: list[_CategoryRule],
) -> dict[str, _BucketAccumulator]:
    """Walk `root` and accumulate per-category sizes/counts."""

    buckets: dict[str, _BucketAccumulator] = {}
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            absolute = Path(dirpath) / filename
            try:
                size = absolute.stat().st_size
            except OSError:
                continue
            rel = str(absolute.relative_to(root))
            category = _classify(rel, rules) or _UNCATEGORIZED
            bucket = buckets.setdefault(category, _BucketAccumulator(category))
            bucket.size_bytes += size
            bucket.file_count += 1
    return buckets


def scan(roots: list[Path], rules_path: Path) -> None:
    """Scan `roots`, emit one summary event and one bucket event per category."""

    rules, uncategorized_retention = _load_rules(rules_path)

    scan_id = uuid.uuid4().hex[:12]
    started_at = time.perf_counter()

    aggregated: dict[str, _BucketAccumulator] = {}
    for root in roots:
        if not root.is_dir():
            _LOG.warning("Storage scan root %s is not a directory.", root)
            continue
        for category, bucket in _walk_root(root, rules).items():
            target = aggregated.setdefault(category, _BucketAccumulator(category))
            target.size_bytes += bucket.size_bytes
            target.file_count += bucket.file_count

    duration_ms = max(0, int((time.perf_counter() - started_at) * 1000))

    emit(
        EVENT_STORAGE_SCAN_SUMMARY,
        status=STATUS_SUCCESS,
        payload={
            "scan_id": scan_id,
            "roots_scanned_count": len(roots),
            "buckets_emitted_count": len(aggregated),
            "duration_ms": duration_ms,
        },
    )

    for category, bucket in aggregated.items():
        retention = _retention_for(category, rules, uncategorized_retention)
        emit(
            EVENT_STORAGE_SCAN_BUCKET,
            status=STATUS_INFO,
            payload={
                "bucket_id": f"{scan_id}-{category}",
                "category": category,
                "path": str(roots[0]) if roots else "",
                "size_bytes": bucket.size_bytes,
                "file_count": bucket.file_count,
                "scan_id": scan_id,
                "retention": retention,
                "is_reclaimable": retention != "keep",
                "reason": retention,
            },
        )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pipe.telemetry.storage_scan",
        description="Scan storage roots and emit per-category bucket events.",
    )
    parser.add_argument(
        "roots",
        nargs="+",
        type=Path,
        help="One or more directory roots to scan.",
    )
    parser.add_argument(
        "--rules-file",
        type=Path,
        default=_DEFAULT_RULES_PATH,
        help="Path to the JSON rule file (default: bundled v1 rules).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (default behavior under systemd timer).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    scan(args.roots, args.rules_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["scan", "main"]
