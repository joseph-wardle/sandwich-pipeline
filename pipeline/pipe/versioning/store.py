"""Low-level version manifest and backup storage helpers.

Current v1 storage stays intentionally compatible with the asset manifest shape:

```
{
  "schema_version": 1,
  "asset": {...},  # legacy asset metadata block kept for compatibility
  "dcc": {
    "<dcc>": {
      "current": {...},
      "history": [...]
    }
  }
}
```

The code in this module is domain-agnostic even though the serialized manifest still
preserves the asset-compatible layout for the first extraction step.
"""

from __future__ import annotations

import datetime
import getpass
import hashlib
import json
import logging
import os
import platform
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator, Optional

from .model import BackupResult, VersionOwner, VersionRecord

_fcntl: ModuleType | None
try:
    import fcntl as _fcntl
except Exception:  # pragma: no cover - platform dependent
    _fcntl = None

log = logging.getLogger(__name__)

MANIFEST_SCHEMA_VERSION = 1
DEFAULT_MANIFEST_FILENAME = "asset_manifest.json"

_VERSION_RE_TEMPLATE = r"^{stem}\.v(?P<ver>\d+)\.{ext}$"
_VERSIONED_STEM_RE = re.compile(r"^(?P<base>.+)\.v(?P<ver>\d+)$")
_SIGNATURE_KEY = "signature"
_SIGNATURE_HASH_KEY = "hash"
_SIGNATURE_HASH_ALGO_KEY = "hash_algo"
_SIGNATURE_SIZE_KEY = "size"
_SIGNATURE_MTIME_NS_KEY = "mtime_ns"
_EXTRA_CHANGED_KEY = "changed"
_EXTRA_VARIANT_KEY = "variant"
_EXTRA_PUBLISH_PATH_KEY = "publish_path"


def _utc_now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _compact_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def _compact_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _compact_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _safe_user() -> Optional[str]:
    try:
        return os.getlogin()
    except Exception:
        try:
            return getpass.getuser()
        except Exception:
            return None


def _safe_host() -> Optional[str]:
    try:
        return platform.node() or None
    except Exception:
        return None


def _legacy_asset_payload(owner: VersionOwner | None) -> dict[str, Any]:
    owner_kind = _compact_text(owner.kind) if owner is not None else None
    if owner is None or (owner_kind or "").lower() != "asset":
        return {}
    return _compact_dict(
        {
            "name": _compact_text(owner.display_name) or _compact_text(owner.code),
            "path": _compact_text(owner.path),
            "id": owner.id,
        }
    )


def get_manifest_path(
    root_path: Path, *, filename: str = DEFAULT_MANIFEST_FILENAME
) -> Path:
    return root_path / filename


def build_manifest(*, owner: VersionOwner | None = None) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "asset": _legacy_asset_payload(owner),
        "dcc": {},
    }


def _ensure_manifest_base(manifest: dict[str, Any]) -> dict[str, Any]:
    manifest.setdefault("schema_version", MANIFEST_SCHEMA_VERSION)
    manifest.setdefault("asset", {})
    manifest.setdefault("dcc", {})
    return manifest


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        return build_manifest()
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            return _ensure_manifest_base(json.load(handle))
    except Exception as exc:
        log.error("Failed to read manifest at %s: %s", manifest_path, exc)
        return build_manifest()


def save_manifest(manifest_path: Path, manifest: dict[str, Any]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with _manifest_write_lock(manifest_path):
        temp_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_path, manifest_path)


@contextmanager
def _manifest_write_lock(manifest_path: Path) -> Iterator[None]:
    if _fcntl is None:
        yield
        return

    lock_path = manifest_path.with_suffix(manifest_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        _fcntl.flock(lock_handle.fileno(), _fcntl.LOCK_EX)
        try:
            yield
        finally:
            _fcntl.flock(lock_handle.fileno(), _fcntl.LOCK_UN)


def _parse_version_from_name(stem: str, ext: str, name: str) -> Optional[int]:
    pattern = _VERSION_RE_TEMPLATE.format(stem=re.escape(stem), ext=re.escape(ext))
    match = re.match(pattern, name)
    if not match:
        return None
    try:
        return int(match.group("ver"))
    except Exception:
        return None


def _normalize_backup_stem(stem: str, source_path: Path) -> str:
    versioned_match = _VERSIONED_STEM_RE.match(stem)
    if not versioned_match:
        return stem

    base_stem = versioned_match.group("base")
    version_token = versioned_match.group("ver")
    log.warning(
        "Detected versioned source stem '%s' from %s; normalizing stem to '%s' "
        "to avoid nested backup names (source version token: v%s).",
        stem,
        source_path,
        base_stem,
        version_token,
    )
    return base_stem


def list_versions(backup_dir: Path, stem: str, ext: str) -> list[int]:
    ext = ext.lstrip(".")
    if not backup_dir.exists():
        return []
    versions: list[int] = []
    for item in backup_dir.iterdir():
        if not item.is_file():
            continue
        version = _parse_version_from_name(stem, ext, item.name)
        if version is not None:
            versions.append(version)
    return sorted(versions)


def next_version(backup_dir: Path, stem: str, ext: str) -> int:
    versions = list_versions(backup_dir, stem, ext)
    return (max(versions) + 1) if versions else 1


def versioned_filename(stem: str, ext: str, version: int, padding: int = 3) -> str:
    ext = ext.lstrip(".")
    return f"{stem}.v{version:0{padding}d}.{ext}"


def compute_signature(
    path: Path,
    *,
    use_hash: bool = False,
    hash_algo: str = "sha256",
    chunk_size: int = 1024 * 1024,
) -> dict[str, Any]:
    """Return a file signature for copy-on-write checks."""
    stat = path.stat()
    signature: dict[str, Any] = {
        _SIGNATURE_SIZE_KEY: stat.st_size,
        _SIGNATURE_MTIME_NS_KEY: stat.st_mtime_ns,
    }

    if use_hash:
        hasher = hashlib.new(hash_algo)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(chunk_size), b""):
                hasher.update(chunk)
        signature[_SIGNATURE_HASH_KEY] = hasher.hexdigest()
        signature[_SIGNATURE_HASH_ALGO_KEY] = hash_algo

    return signature


def _signature_matches(
    a: Optional[dict[str, Any]], b: Optional[dict[str, Any]]
) -> bool:
    if not a or not b:
        return False
    for key in (_SIGNATURE_SIZE_KEY, _SIGNATURE_MTIME_NS_KEY):
        if a.get(key) != b.get(key):
            return False
    if _SIGNATURE_HASH_KEY in a or _SIGNATURE_HASH_KEY in b:
        return a.get(_SIGNATURE_HASH_KEY) == b.get(_SIGNATURE_HASH_KEY) and a.get(
            _SIGNATURE_HASH_ALGO_KEY
        ) == b.get(_SIGNATURE_HASH_ALGO_KEY)
    return True


def backup_file(
    source_path: Path,
    backup_dir: Path,
    *,
    stem: Optional[str] = None,
    ext: Optional[str] = None,
    version: Optional[int] = None,
    padding: int = 3,
    ensure_exists: bool = True,
) -> Optional[Path]:
    """Copy a file into the backup directory using a versioned filename."""
    if ensure_exists and not source_path.exists():
        log.warning("Backup skipped; source missing: %s", source_path)
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)

    resolved_stem = stem or source_path.stem
    resolved_ext = (ext or source_path.suffix.lstrip(".")) or "dat"
    resolved_stem = _normalize_backup_stem(resolved_stem, source_path)

    next_ver = version or next_version(backup_dir, resolved_stem, resolved_ext)
    target_name = versioned_filename(resolved_stem, resolved_ext, next_ver, padding)
    target_path = backup_dir / target_name
    temp_path = backup_dir / f".{target_name}.tmp"

    shutil.copy2(source_path, temp_path)
    os.replace(temp_path, target_path)
    return target_path


def backup_if_changed(
    source_path: Path,
    backup_dir: Path,
    manifest_path: Path,
    *,
    dcc: str,
    stem: Optional[str] = None,
    ext: Optional[str] = None,
    version: Optional[int] = None,
    padding: int = 3,
    ensure_exists: bool = True,
    use_hash: bool = False,
    title: Optional[str] = None,
    context: Optional[str] = None,
    note: Optional[str] = None,
    tool_version: Optional[str] = None,
    owner: VersionOwner | None = None,
    variant: Optional[str] = None,
    publish_path: Optional[Path] = None,
    extra: Optional[dict[str, Any]] = None,
) -> Optional[BackupResult]:
    """Copy a file into backup storage only when the source signature changed."""
    if ensure_exists and not source_path.exists():
        log.warning("Backup skipped; source missing: %s", source_path)
        return None

    signature = compute_signature(source_path, use_hash=use_hash)
    manifest = load_manifest(manifest_path)
    dcc_block = manifest.get("dcc", {}).get(dcc, {})
    current = dcc_block.get("current") or {}
    previous_signature = (current.get("extra") or {}).get(_SIGNATURE_KEY)

    changed = not _signature_matches(previous_signature, signature)
    backup_path: Optional[Path] = None
    resolved_version: Optional[int] = current.get("version")

    resolved_stem = stem or source_path.stem
    resolved_ext = (ext or source_path.suffix.lstrip(".")) or "dat"
    resolved_stem = _normalize_backup_stem(resolved_stem, source_path)

    if changed:
        resolved_version = version or next_version(
            backup_dir, resolved_stem, resolved_ext
        )
        backup_path = backup_file(
            source_path,
            backup_dir,
            stem=resolved_stem,
            ext=resolved_ext,
            version=resolved_version,
            padding=padding,
            ensure_exists=False,
        )
    else:
        existing_backup = current.get("backup_file")
        if existing_backup:
            backup_path = Path(existing_backup)

    extra_payload = dict(extra or {})
    extra_payload[_SIGNATURE_KEY] = signature
    extra_payload[_EXTRA_CHANGED_KEY] = changed
    if variant:
        extra_payload[_EXTRA_VARIANT_KEY] = variant
    if publish_path:
        extra_payload[_EXTRA_PUBLISH_PATH_KEY] = str(publish_path)

    manifest = record_publish(
        manifest_path,
        dcc=dcc,
        source_path=source_path,
        backup_path=backup_path,
        version=resolved_version,
        title=title,
        context=context,
        note=note,
        tool_version=tool_version,
        extra=extra_payload,
        owner=owner,
    )

    return BackupResult(
        changed=changed,
        signature=signature,
        backup_path=backup_path,
        version=resolved_version,
        manifest=manifest,
    )


def record_publish(
    manifest_path: Path,
    *,
    dcc: str,
    source_path: Optional[Path] = None,
    backup_path: Optional[Path] = None,
    version: Optional[int] = None,
    user: Optional[str] = None,
    host: Optional[str] = None,
    title: Optional[str] = None,
    context: Optional[str] = None,
    note: Optional[str] = None,
    tool_version: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
    owner: VersionOwner | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    _ensure_manifest_base(manifest)

    owner_payload = _legacy_asset_payload(owner)
    if owner_payload:
        manifest["asset"].update(owner_payload)

    entry = _compact_dict(
        {
            "timestamp": _utc_now_iso(),
            "user": user or _safe_user(),
            "host": host or _safe_host(),
            "source_file": str(source_path) if source_path else None,
            "backup_file": str(backup_path) if backup_path else None,
            "version": version,
            "title": title,
            "context": context,
            "note": note,
            "tool_version": tool_version,
            "extra": extra,
        }
    )

    dcc_block = manifest["dcc"].setdefault(dcc, {"current": None, "history": []})
    dcc_block["current"] = entry
    dcc_block.setdefault("history", []).append(entry)

    save_manifest(manifest_path, manifest)
    return manifest


def history_as_records(manifest: dict[str, Any], dcc: str) -> list[VersionRecord]:
    dcc_payload = manifest.get("dcc")
    if not isinstance(dcc_payload, dict):
        return []

    dcc_block = dcc_payload.get(dcc)
    if not isinstance(dcc_block, dict):
        return []

    history_payload = dcc_block.get("history")
    if not isinstance(history_payload, list):
        return []

    records: list[VersionRecord] = []
    for entry in reversed(history_payload):
        if not isinstance(entry, dict):
            continue
        backup_value = _compact_text(entry.get("backup_file"))
        records.append(
            VersionRecord(
                version=_compact_int(entry.get("version")),
                title=_compact_text(entry.get("title")),
                note=_compact_text(entry.get("note")),
                context=_compact_text(entry.get("context")),
                user=_compact_text(entry.get("user")),
                timestamp=_compact_text(entry.get("timestamp")),
                backup_path=Path(backup_value) if backup_value else None,
                source_file=_compact_text(entry.get("source_file")),
            )
        )
    return records


def version_label(version: int | None) -> str:
    """Format a version number as a zero-padded label (for example ``v003``)."""
    if version is None:
        return "-"
    return f"v{version:03d}"


__all__ = [
    "DEFAULT_MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "backup_file",
    "backup_if_changed",
    "build_manifest",
    "compute_signature",
    "get_manifest_path",
    "history_as_records",
    "list_versions",
    "load_manifest",
    "next_version",
    "record_publish",
    "save_manifest",
    "version_label",
    "versioned_filename",
]
