"""Storage scan collector for aggregated telemetry buckets.

The scanner intentionally emits only aggregated events:
- ``storage.scan.summary`` (one per scan run)
- ``storage.scan.bucket`` (many per scan run)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Sequence

from . import events
from .context import extract_scope, new_action_id
from .contract import serialize_event
from .emit import emit
from .registry import STATUS_INFO, STATUS_SUCCESS

ScopeType = str
Category = str

STORAGE_RULES_ENVVAR = "PIPE_TELEMETRY_STORAGE_RULES_FILE"
BUILTIN_STORAGE_RULES_FILE = (
    Path(__file__).resolve().parent / "rules" / "storage_categories.v1.json"
)
DEFAULT_CATEGORY = "other"


@dataclass(frozen=True)
class StorageCategoryRule:
    """Ordered classification rule for one storage category."""

    category: Category
    path_regexes: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class _CompiledStorageCategoryRule:
    category: Category
    path_patterns: tuple[re.Pattern[str], ...]
    suffixes: tuple[str, ...]


DEFAULT_CATEGORY_RULES: tuple[StorageCategoryRule, ...] = (
    StorageCategoryRule("render", path_regexes=(r"(^|/)renders?(/|$)",)),
    StorageCategoryRule(
        "fx_cache",
        path_regexes=(r"(^|/)fx(/|$)",),
        suffixes=(".vdb", ".bgeo", ".bgeo.sc", ".sim"),
    ),
    StorageCategoryRule(
        "texture",
        path_regexes=(r"(^|/)tex(?:ture)?s?(/|$)",),
        suffixes=(
            ".tex",
            ".tx",
            ".rat",
            ".png",
            ".jpg",
            ".jpeg",
            ".tif",
            ".tiff",
            ".exr",
        ),
    ),
    StorageCategoryRule("publish", path_regexes=(r"(^|/)publish(/|$)",)),
    StorageCategoryRule(
        "playblast",
        path_regexes=(r"(^|/)playblasts?(/|$)",),
        suffixes=(".mov", ".mp4"),
    ),
)


def _utc_iso(timestamp: datetime) -> str:
    return timestamp.isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalize_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def _normalize_suffix(raw_suffix: str) -> str:
    suffix = str(raw_suffix).strip().lower()
    if not suffix:
        raise ValueError("suffix must be non-empty")
    if not suffix.startswith("."):
        suffix = "." + suffix
    return suffix


def _normalize_rule(rule: StorageCategoryRule) -> StorageCategoryRule:
    category = str(rule.category).strip()
    if not category:
        raise ValueError("category must be non-empty")

    path_regexes = tuple(
        candidate.strip()
        for candidate in (str(item) for item in rule.path_regexes)
        if candidate.strip()
    )
    suffixes = tuple(
        _normalize_suffix(item) for item in rule.suffixes if str(item).strip()
    )

    if not path_regexes and not suffixes:
        raise ValueError(
            f"storage category rule '{category}' must define path_regexes or suffixes"
        )

    return StorageCategoryRule(
        category=category,
        path_regexes=path_regexes,
        suffixes=suffixes,
    )


def _coerce_category_rules(
    rules: Optional[Sequence[StorageCategoryRule]] = None,
) -> tuple[StorageCategoryRule, ...]:
    raw_rules = default_category_rules() if rules is None else tuple(rules)
    if not raw_rules:
        raise ValueError("at least one storage category rule is required")
    normalized_rules = tuple(_normalize_rule(rule) for rule in raw_rules)
    _compile_category_rules(normalized_rules)
    return normalized_rules


@lru_cache(maxsize=16)
def _compile_category_rules(
    rules: tuple[StorageCategoryRule, ...],
) -> tuple[_CompiledStorageCategoryRule, ...]:
    compiled_rules: list[_CompiledStorageCategoryRule] = []
    for rule in rules:
        try:
            compiled_patterns = tuple(
                re.compile(pattern, re.IGNORECASE) for pattern in rule.path_regexes
            )
        except re.error as exc:
            raise ValueError(
                f"invalid regex in storage category rule '{rule.category}': {exc}"
            ) from exc
        compiled_rules.append(
            _CompiledStorageCategoryRule(
                category=rule.category,
                path_patterns=compiled_patterns,
                suffixes=tuple(sorted(set(rule.suffixes))),
            )
        )
    return tuple(compiled_rules)


@lru_cache(maxsize=1)
def default_category_rules() -> tuple[StorageCategoryRule, ...]:
    """Return default ordered category rules from the built-in JSON config."""

    try:
        return load_category_rules_file(BUILTIN_STORAGE_RULES_FILE)
    except Exception:
        return DEFAULT_CATEGORY_RULES


def load_category_rules_file(path: Path) -> tuple[StorageCategoryRule, ...]:
    """Load ordered category rules from JSON file."""

    data = json.loads(path.read_text(encoding="utf-8"))
    raw_rules = data.get("rules") if isinstance(data, dict) else data
    if not isinstance(raw_rules, list):
        raise ValueError("rules file must be a list or an object with a 'rules' list")
    if not raw_rules:
        raise ValueError("rules file must contain at least one rule")

    parsed_rules: list[StorageCategoryRule] = []
    for index, item in enumerate(raw_rules):
        if not isinstance(item, dict):
            raise ValueError(f"rule {index} must be an object")

        category = item.get("category")  # type: ignore
        path_regexes = item.get("path_regexes", [])  # type: ignore
        suffixes = item.get("suffixes", [])  # type: ignore

        if not isinstance(category, str):
            raise ValueError(f"rule {index} category must be a string")
        if not isinstance(path_regexes, list):
            raise ValueError(f"rule {index} path_regexes must be a list")
        if not isinstance(suffixes, list):
            raise ValueError(f"rule {index} suffixes must be a list")

        parsed_rules.append(
            StorageCategoryRule(
                category=category,
                path_regexes=tuple(str(item) for item in path_regexes),
                suffixes=tuple(str(item) for item in suffixes),
            )
        )
    return _coerce_category_rules(parsed_rules)


def resolve_category_rules(
    rules_file: Optional[Path] = None,
) -> tuple[StorageCategoryRule, ...]:
    """Resolve scanner category rules from explicit path, env var, or defaults."""

    resolved_rules_file = rules_file
    if resolved_rules_file is None:
        env_value = os.getenv(STORAGE_RULES_ENVVAR, "").strip()
        if env_value:
            resolved_rules_file = Path(env_value).expanduser()

    if resolved_rules_file is None:
        return default_category_rules()
    return load_category_rules_file(resolved_rules_file.expanduser())


def _classify_with_rules(
    normalized_path_lower: str,
    file_name_lower: str,
    compiled_rules: Sequence[_CompiledStorageCategoryRule],
) -> Category:
    for rule in compiled_rules:
        if any(pattern.search(normalized_path_lower) for pattern in rule.path_patterns):
            return rule.category
        if any(file_name_lower.endswith(suffix) for suffix in rule.suffixes):
            return rule.category
    return DEFAULT_CATEGORY


def classify_path(
    path: Path, *, rules: Optional[Sequence[StorageCategoryRule]] = None
) -> Category:
    """Classify a production file path into one telemetry storage category."""

    resolved_rules = _coerce_category_rules(rules)
    compiled_rules = _compile_category_rules(resolved_rules)
    normalized_path_lower = _normalize_path(path).lower()
    file_name_lower = path.name.lower()
    return _classify_with_rules(normalized_path_lower, file_name_lower, compiled_rules)


def _bucket_path(root: Path, file_path: Path) -> Path:
    try:
        relative = file_path.relative_to(root)
    except ValueError:
        return root
    if not relative.parts:
        return root
    return root / relative.parts[0]


def _bucket_id(scope_type: str, scope_code: str, category: str, path: str) -> str:
    raw = f"{scope_type}|{scope_code}|{category}|{path}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class BucketKey:
    scope_type: ScopeType
    scope_code: str
    category: Category
    path: str


@dataclass
class BucketMetrics:
    size_bytes: int = 0
    file_count: int = 0
    dirs: set[str] = field(default_factory=set)

    @property
    def dir_count(self) -> int:
        return len(self.dirs)

    def add_file(self, directory: str, size_bytes: int) -> None:
        self.size_bytes += size_bytes
        self.file_count += 1
        self.dirs.add(directory)


@dataclass
class StorageScanResult:
    scan_id: str
    scan_window_start_utc: str
    scan_window_end_utc: str
    roots_scanned_count: int
    buckets: dict[BucketKey, BucketMetrics]
    skipped_files: int
    scan_duration_ms: int

    @property
    def buckets_emitted_count(self) -> int:
        return len(self.buckets)

    @property
    def size_bytes_total(self) -> int:
        return sum(item.size_bytes for item in self.buckets.values())

    @property
    def file_count_total(self) -> int:
        return sum(item.file_count for item in self.buckets.values())

    @property
    def dir_count_total(self) -> int:
        return sum(item.dir_count for item in self.buckets.values())


def scan_storage(
    roots: Iterable[Path],
    *,
    scope_type: str,
    scope_code: str,
    category_rules: Optional[Sequence[StorageCategoryRule]] = None,
    scan_window_start_utc: Optional[str] = None,
    scan_window_end_utc: Optional[str] = None,
) -> StorageScanResult:
    """Scan roots and return aggregated usage buckets."""

    resolved_rules = _coerce_category_rules(category_rules)
    compiled_rules = _compile_category_rules(resolved_rules)

    scan_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    window_start = scan_window_start_utc or _utc_iso(started_at)
    buckets: dict[BucketKey, BucketMetrics] = {}
    skipped_files = 0
    roots_scanned_count = 0

    for root in roots:
        resolved_root = root.expanduser()
        if not resolved_root.exists() or not resolved_root.is_dir():
            continue

        roots_scanned_count += 1

        def _walk_error(_: OSError) -> None:
            nonlocal skipped_files
            skipped_files += 1

        for dirpath, _, filenames in os.walk(resolved_root, onerror=_walk_error):
            dir_path = Path(dirpath)
            dir_str = _normalize_path(dir_path)
            for filename in filenames:
                file_path = dir_path / filename
                try:
                    stat = file_path.stat()
                except OSError:
                    skipped_files += 1
                    continue

                normalized_file_path_lower = _normalize_path(file_path).lower()
                category = _classify_with_rules(
                    normalized_file_path_lower,
                    filename.lower(),
                    compiled_rules,
                )

                bucket_path = _normalize_path(_bucket_path(resolved_root, file_path))
                key = BucketKey(
                    scope_type=scope_type,
                    scope_code=scope_code,
                    category=category,
                    path=bucket_path,
                )
                metric = buckets.setdefault(key, BucketMetrics())
                metric.add_file(dir_str, stat.st_size)

    ended_at = datetime.now(timezone.utc)
    duration_ms = int((ended_at - started_at).total_seconds() * 1000)

    return StorageScanResult(
        scan_id=scan_id,
        scan_window_start_utc=window_start,
        scan_window_end_utc=scan_window_end_utc or _utc_iso(ended_at),
        roots_scanned_count=roots_scanned_count,
        buckets=buckets,
        skipped_files=skipped_files,
        scan_duration_ms=duration_ms,
    )


def build_storage_events(
    result: StorageScanResult,
    *,
    scope: Optional[dict[str, str]] = None,
    action_id: Optional[str] = None,
) -> list[dict[str, object]]:
    """Build ``storage.scan.summary`` and ``storage.scan.bucket`` events."""

    scope_data = scope or {}
    resolved_action_id = action_id or new_action_id()
    events_out: list[dict[str, object]] = []

    for key in sorted(
        result.buckets.keys(),
        key=lambda item: (item.scope_type, item.scope_code, item.category, item.path),
    ):
        metric = result.buckets[key]
        payload = {
            "scan_id": result.scan_id,
            "bucket_id": _bucket_id(
                key.scope_type, key.scope_code, key.category, key.path
            ),
            "scope_type": key.scope_type,
            "scope_code": key.scope_code,
            "category": key.category,
            "path": key.path,
            "scan_window_start_utc": result.scan_window_start_utc,
            "scan_window_end_utc": result.scan_window_end_utc,
        }
        bucket_event = emit(
            events.EVENT_STORAGE_SCAN_BUCKET,
            status=STATUS_INFO,
            payload=payload,
            metrics={
                "size_bytes": metric.size_bytes,
                "file_count": metric.file_count,
                "dir_count": metric.dir_count,
            },
            scope=scope_data,
            action_id=resolved_action_id,
        )
        if bucket_event is not None:
            events_out.append(bucket_event)

    summary_event = emit(
        events.EVENT_STORAGE_SCAN_SUMMARY,
        status=STATUS_SUCCESS,
        payload={
            "scan_id": result.scan_id,
            "scan_window_start_utc": result.scan_window_start_utc,
            "scan_window_end_utc": result.scan_window_end_utc,
            "roots_scanned_count": result.roots_scanned_count,
            "buckets_emitted_count": result.buckets_emitted_count,
            "scan_duration_ms": result.scan_duration_ms,
            "skipped_files": result.skipped_files,
        },
        metrics={
            "size_bytes_total": result.size_bytes_total,
            "file_count_total": result.file_count_total,
            "dir_count_total": result.dir_count_total,
        },
        scope=scope_data,
        action_id=resolved_action_id,
    )
    if summary_event is not None:
        events_out.insert(0, summary_event)
    return events_out


def _scope_from_args(args: argparse.Namespace) -> dict[str, str]:
    return extract_scope(vars(args))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan storage roots and emit aggregated telemetry events."
    )
    parser.add_argument("roots", nargs="+", help="Root directories to scan")
    parser.add_argument(
        "--scope-type",
        default="show",
        choices=("show", "sequence", "shot", "asset", "department"),
        help="Bucket scope dimension type",
    )
    parser.add_argument(
        "--scope-code",
        required=True,
        help="Bucket scope dimension code (for example show code).",
    )
    parser.add_argument(
        "--rules-file",
        type=Path,
        help=(
            "Optional JSON file with ordered category rules. "
            f"If omitted, {STORAGE_RULES_ENVVAR} is used when set."
        ),
    )
    parser.add_argument("--scan-window-start-utc")
    parser.add_argument("--scan-window-end-utc")
    parser.add_argument("--show")
    parser.add_argument("--sequence")
    parser.add_argument("--shot")
    parser.add_argument("--asset")
    parser.add_argument("--department")
    parser.add_argument("--task")
    parser.add_argument(
        "--print-events",
        action="store_true",
        help="Print each JSON event to stdout.",
    )
    parser.add_argument(
        "--jsonl-out",
        type=Path,
        help="Optional output path for JSONL event output.",
    )
    args = parser.parse_args(argv)

    try:
        category_rules = resolve_category_rules(args.rules_file)
    except Exception as exc:
        print(f"Failed to load storage category rules: {exc}", file=sys.stderr)
        return 2

    roots = [Path(root).expanduser() for root in args.roots]
    result = scan_storage(
        roots,
        scope_type=args.scope_type,
        scope_code=args.scope_code,
        category_rules=category_rules,
        scan_window_start_utc=args.scan_window_start_utc,
        scan_window_end_utc=args.scan_window_end_utc,
    )
    events_out = build_storage_events(result, scope=_scope_from_args(args))

    if args.jsonl_out is not None:
        args.jsonl_out.parent.mkdir(parents=True, exist_ok=True)
        with args.jsonl_out.open("w", encoding="utf-8") as handle:
            for event in events_out:
                handle.write(serialize_event(event))
                handle.write("\n")

    if args.print_events:
        for event in events_out:
            print(serialize_event(event))
    else:
        print(
            "scan_id={scan_id} roots={roots} buckets={buckets} size_bytes_total={size}".format(
                scan_id=result.scan_id,
                roots=result.roots_scanned_count,
                buckets=result.buckets_emitted_count,
                size=result.size_bytes_total,
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
