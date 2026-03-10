"""Low-level version manifest and backup storage helpers.

Authoritative v1 manifest shape:

```
{
  "schema_version": 1,
  "owner": {...},
  "asset": {...},   # legacy compatibility for existing asset tooling
  "streams": {
    "<stream_key>": {
      "dcc": "maya",
      "stem": "model",
      "ext": "mb",
      "label": "model.mb",
      "working_file": "model.mb",
      "current": {...},
      "history": [...]
    }
  },
  "dcc": {...}      # legacy read compatibility for pre-stream manifests
}
```

New writes target ``streams``. Reads fall back to legacy ``dcc`` entries only when a
stream has not been written yet.

Manifest filename divergence
----------------------------
The manifest filename is chosen by each domain adapter, not by this module:

- Assets use ``ASSET_MANIFEST_FILENAME`` (``"asset_manifest.json"``).  This name
  predates the unified versioning system and must not be changed without migrating
  every existing asset directory on disk.
- Shots and environments both use ``VERSION_MANIFEST_FILENAME``
  (``"version_manifest.json"``), introduced alongside the unified system.

A future normalisation pass could migrate asset manifests to ``VERSION_MANIFEST_FILENAME``
and unify the two, but that is out of scope until all asset roots have been converted.

History entries may also include optional compound snapshot metadata:

```
{
  "backup_file": "/abs/path/to/primary/file",
  "backup_root": "/abs/path/to/version/bundle",
  "backup_members": [
    "rlo/A_010.mb",
    "maya_root.usd",
    "set/maya_override.usd"
  ]
}
```
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

from .model import (
    BackupResult,
    VersionOwner,
    VersionRecord,
    stream_filename,
    stream_key_for,
)

_fcntl: ModuleType | None
try:
    import fcntl as _fcntl
except Exception:  # pragma: no cover - platform dependent
    _fcntl = None

log = logging.getLogger(__name__)

MANIFEST_SCHEMA_VERSION = 1

# Asset directories use "asset_manifest.json" — a legacy name predating the
# unified versioning system that must not be renamed without a disk migration.
ASSET_MANIFEST_FILENAME = "asset_manifest.json"

# Shot and environment directories use "version_manifest.json" — introduced
# together with the unified system and shared by both domain adapters.
VERSION_MANIFEST_FILENAME = "version_manifest.json"

_VERSION_RE_TEMPLATE = r"^{stem}\.v(?P<ver>\d+)\.{ext}$"
_VERSIONED_STEM_RE = re.compile(r"^(?P<base>.+)\.v(?P<ver>\d+)$")
_BUNDLE_VERSION_RE = re.compile(r"^v(?P<ver>\d+)$")
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


def _owner_payload(owner: VersionOwner | None) -> dict[str, Any]:
    if owner is None:
        return {}
    return _compact_dict(
        {
            "kind": _compact_text(owner.kind),
            "code": _compact_text(owner.code),
            "display_name": _compact_text(owner.display_name),
            "path": _compact_text(owner.path),
            "id": owner.id,
        }
    )


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
    root_path: Path, *, filename: str = ASSET_MANIFEST_FILENAME
) -> Path:
    return root_path / filename


def build_manifest(*, owner: VersionOwner | None = None) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "owner": _owner_payload(owner),
        "asset": _legacy_asset_payload(owner),
        "streams": {},
        "dcc": {},
    }


def _ensure_manifest_base(manifest: dict[str, Any]) -> dict[str, Any]:
    manifest.setdefault("schema_version", MANIFEST_SCHEMA_VERSION)
    manifest.setdefault("owner", {})
    manifest.setdefault("asset", {})
    manifest.setdefault("streams", {})
    manifest.setdefault("dcc", {})
    _backfill_owner_from_legacy_asset(manifest)
    return manifest


def _backfill_owner_from_legacy_asset(manifest: dict[str, Any]) -> None:
    owner_payload = manifest.get("owner")
    if isinstance(owner_payload, dict) and owner_payload:
        return

    legacy_asset = manifest.get("asset")
    if not isinstance(legacy_asset, dict) or not legacy_asset:
        return

    display_name = _compact_text(legacy_asset.get("name"))
    manifest["owner"] = _compact_dict(
        {
            "kind": "asset",
            "code": display_name or _compact_text(legacy_asset.get("path")) or "asset",
            "display_name": display_name,
            "path": _compact_text(legacy_asset.get("path")),
            "id": _compact_int(legacy_asset.get("id")),
        }
    )


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


def _parse_version_from_name(*, stem: str, ext: str, name: str) -> Optional[int]:
    """Return the version number embedded in a versioned filename, or ``None``.

    Matches filenames of the form ``<stem>.v<N>.<ext>`` where N is one or more
    digits.  Used by both this module and ``service.py`` — the authoritative
    definition lives here so there is only one regex template.
    """
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


def _resolve_stream_identity(
    dcc: str,
    *,
    stream_key: Optional[str] = None,
    stem: Optional[str] = None,
    ext: Optional[str] = None,
    source_path: Optional[Path] = None,
    backup_path: Optional[Path] = None,
) -> tuple[str, str, str]:
    source_candidate = source_path or backup_path
    resolved_stem = stem or (source_candidate.stem if source_candidate else "stream")
    if source_candidate is not None:
        resolved_stem = _normalize_backup_stem(resolved_stem, source_candidate)

    resolved_ext = ext or (
        source_candidate.suffix.lstrip(".") if source_candidate is not None else "dat"
    )
    normalized_ext = (resolved_ext or "dat").lstrip(".")
    normalized_stream_key = _compact_text(stream_key) or stream_key_for(
        dcc, resolved_stem, normalized_ext
    )
    return normalized_stream_key, resolved_stem, normalized_ext


def _serialize_working_file(
    manifest_path: Path,
    working_path: Optional[Path],
    *,
    stem: str,
    ext: str,
) -> str:
    candidate = working_path or (manifest_path.parent / stream_filename(stem, ext))
    try:
        relative = candidate.resolve().relative_to(manifest_path.parent.resolve())
        return relative.as_posix()
    except Exception:
        return str(candidate)


def _stream_block_for_read(
    manifest: dict[str, Any],
    *,
    stream_key: str,
    fallback_dcc: Optional[str] = None,
) -> dict[str, Any] | None:
    streams_payload = manifest.get("streams")
    if isinstance(streams_payload, dict):
        stream_block = streams_payload.get(stream_key)
        if isinstance(stream_block, dict):
            return stream_block

    if not fallback_dcc:
        return None

    dcc_payload = manifest.get("dcc")
    if not isinstance(dcc_payload, dict):
        return None

    dcc_block = dcc_payload.get(fallback_dcc)
    if isinstance(dcc_block, dict):
        return dcc_block
    return None


def _stream_block_for_write(
    manifest: dict[str, Any],
    *,
    stream_key: str,
    dcc: str,
    stem: str,
    ext: str,
    label: Optional[str],
    working_file: Optional[str],
) -> dict[str, Any]:
    streams_payload = manifest.setdefault("streams", {})
    if not isinstance(streams_payload, dict):
        streams_payload = {}
        manifest["streams"] = streams_payload

    stream_block = streams_payload.setdefault(stream_key, {})
    if not isinstance(stream_block, dict):
        stream_block = {}
        streams_payload[stream_key] = stream_block

    stream_block.update(
        _compact_dict(
            {
                "dcc": dcc,
                "stem": stem,
                "ext": ext,
                "label": label,
                "working_file": working_file,
            }
        )
    )
    stream_block.setdefault("current", None)
    history_payload = stream_block.get("history")
    if not isinstance(history_payload, list):
        stream_block["history"] = []
    return stream_block


def _entry_as_record(entry: dict[str, Any]) -> VersionRecord:
    backup_value = _compact_text(entry.get("backup_file"))
    backup_root = _compact_text(entry.get("backup_root"))
    backup_members_payload = entry.get("backup_members")
    backup_members: tuple[str, ...] = ()
    if isinstance(backup_members_payload, list):
        backup_members = tuple(
            member_text
            for member_text in (
                _compact_text(member) for member in backup_members_payload
            )
            if member_text is not None
        )
    return VersionRecord(
        version=_compact_int(entry.get("version")),
        title=_compact_text(entry.get("title")),
        note=_compact_text(entry.get("note")),
        context=_compact_text(entry.get("context")),
        user=_compact_text(entry.get("user")),
        timestamp=_compact_text(entry.get("timestamp")),
        backup_path=Path(backup_value) if backup_value else None,
        source_file=_compact_text(entry.get("source_file")),
        backup_root=Path(backup_root) if backup_root else None,
        backup_members=backup_members,
    )


def list_versions(backup_dir: Path, stem: str, ext: str) -> list[int]:
    ext = ext.lstrip(".")
    if not backup_dir.exists():
        return []
    versions: list[int] = []
    for item in backup_dir.iterdir():
        if not item.is_file():
            continue
        version = _parse_version_from_name(stem=stem, ext=ext, name=item.name)
        if version is not None:
            versions.append(version)
    return sorted(versions)


def next_version(backup_dir: Path, stem: str, ext: str) -> int:
    versions = list_versions(backup_dir, stem, ext)
    return (max(versions) + 1) if versions else 1


def versioned_filename(stem: str, ext: str, version: int, padding: int = 3) -> str:
    ext = ext.lstrip(".")
    return f"{stem}.v{version:0{padding}d}.{ext}"


def bundle_dirname(version: int, padding: int = 3) -> str:
    return f"v{version:0{padding}d}"


def list_bundle_versions(backup_dir: Path) -> list[int]:
    if not backup_dir.exists():
        return []

    versions: list[int] = []
    for item in backup_dir.iterdir():
        if not item.is_dir():
            continue
        match = _BUNDLE_VERSION_RE.match(item.name)
        if not match:
            continue
        try:
            versions.append(int(match.group("ver")))
        except Exception:
            continue
    return sorted(versions)


def next_bundle_version(backup_dir: Path) -> int:
    versions = list_bundle_versions(backup_dir)
    return (max(versions) + 1) if versions else 1


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
    stream_key: Optional[str] = None,
    stem: Optional[str] = None,
    ext: Optional[str] = None,
    stream_label: Optional[str] = None,
    working_path: Optional[Path] = None,
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
    resolved_stream_key, resolved_stem, resolved_ext = _resolve_stream_identity(
        dcc,
        stream_key=stream_key,
        stem=stem,
        ext=ext,
        source_path=source_path,
    )
    stream_block = _stream_block_for_read(
        manifest,
        stream_key=resolved_stream_key,
        fallback_dcc=dcc,
    )
    current = stream_block.get("current") if isinstance(stream_block, dict) else {}
    if not isinstance(current, dict):
        current = {}
    previous_signature = (current.get("extra") or {}).get(_SIGNATURE_KEY)

    changed = not _signature_matches(previous_signature, signature)
    backup_path: Optional[Path] = None
    resolved_version: Optional[int] = _compact_int(current.get("version"))

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
        stream_key=resolved_stream_key,
        stem=resolved_stem,
        ext=resolved_ext,
        stream_label=stream_label,
        working_path=working_path or source_path,
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
    stream_key: Optional[str] = None,
    stem: Optional[str] = None,
    ext: Optional[str] = None,
    stream_label: Optional[str] = None,
    working_path: Optional[Path] = None,
    source_path: Optional[Path] = None,
    backup_path: Optional[Path] = None,
    backup_root: Optional[Path] = None,
    backup_members: Optional[list[str]] = None,
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

    owner_payload = _owner_payload(owner)
    if owner_payload:
        manifest["owner"].update(owner_payload)

    legacy_asset_payload = _legacy_asset_payload(owner)
    if legacy_asset_payload:
        manifest["asset"].update(legacy_asset_payload)

    resolved_stream_key, resolved_stem, resolved_ext = _resolve_stream_identity(
        dcc,
        stream_key=stream_key,
        stem=stem,
        ext=ext,
        source_path=source_path,
        backup_path=backup_path,
    )
    working_file = _serialize_working_file(
        manifest_path,
        working_path,
        stem=resolved_stem,
        ext=resolved_ext,
    )

    entry = _compact_dict(
        {
            "timestamp": _utc_now_iso(),
            "user": user or _safe_user(),
            "host": host or _safe_host(),
            "source_file": str(source_path) if source_path else None,
            "backup_file": str(backup_path) if backup_path else None,
            "backup_root": str(backup_root) if backup_root else None,
            "backup_members": backup_members,
            "version": version,
            "title": title,
            "context": context,
            "note": note,
            "tool_version": tool_version,
            "extra": extra,
        }
    )

    stream_block = _stream_block_for_write(
        manifest,
        stream_key=resolved_stream_key,
        dcc=dcc,
        stem=resolved_stem,
        ext=resolved_ext,
        label=_compact_text(stream_label) or working_file,
        working_file=working_file,
    )
    stream_block["current"] = entry
    stream_block.setdefault("history", []).append(entry)

    save_manifest(manifest_path, manifest)
    return manifest


def history_as_records(
    manifest: dict[str, Any],
    stream_key: str,
    *,
    fallback_dcc: Optional[str] = None,
) -> list[VersionRecord]:
    stream_block = _stream_block_for_read(
        manifest,
        stream_key=stream_key,
        fallback_dcc=fallback_dcc,
    )
    if not isinstance(stream_block, dict):
        return []

    history_payload = stream_block.get("history")
    if not isinstance(history_payload, list):
        return []

    records: list[VersionRecord] = []
    for entry in reversed(history_payload):
        if not isinstance(entry, dict):
            continue
        records.append(_entry_as_record(entry))
    return records


def current_record(
    manifest: dict[str, Any],
    stream_key: str,
    *,
    fallback_dcc: Optional[str] = None,
) -> Optional[VersionRecord]:
    stream_block = _stream_block_for_read(
        manifest,
        stream_key=stream_key,
        fallback_dcc=fallback_dcc,
    )
    if not isinstance(stream_block, dict):
        return None

    current_payload = stream_block.get("current")
    if not isinstance(current_payload, dict):
        return None
    return _entry_as_record(current_payload)


def version_label(version: int | None) -> str:
    """Format a version number as a zero-padded label (for example ``v003``)."""
    if version is None:
        return "-"
    return f"v{version:03d}"


__all__ = [
    "ASSET_MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "VERSION_MANIFEST_FILENAME",
    "backup_file",
    "backup_if_changed",
    "bundle_dirname",
    "build_manifest",
    "compute_signature",
    "current_record",
    "get_manifest_path",
    "history_as_records",
    "list_bundle_versions",
    "list_versions",
    "load_manifest",
    "next_bundle_version",
    "next_version",
    "record_publish",
    "save_manifest",
    "version_label",
    "versioned_filename",
]
