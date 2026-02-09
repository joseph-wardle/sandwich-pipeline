"""Versioning and manifest helpers for asset publishing."""

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .paths import MANIFEST_FILENAME

log = logging.getLogger(__name__)

_VERSION_RE_TEMPLATE = r"^{stem}\.v(?P<ver>\d+)\.{ext}$"
_SIGNATURE_KEY = "signature"
_SIGNATURE_HASH_KEY = "hash"
_SIGNATURE_HASH_ALGO_KEY = "hash_algo"
_SIGNATURE_SIZE_KEY = "size"
_SIGNATURE_MTIME_NS_KEY = "mtime_ns"
_EXTRA_CHANGED_KEY = "changed"
_EXTRA_VARIANT_KEY = "variant"
_EXTRA_PUBLISH_PATH_KEY = "publish_path"


@dataclass(frozen=True)
class BackupResult:
    changed: bool
    signature: dict[str, Any]
    backup_path: Optional[Path]
    version: Optional[int]
    manifest: dict[str, Any]


def _utc_now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _compact_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


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


def get_manifest_path(asset_root: Path) -> Path:
    return asset_root / MANIFEST_FILENAME


def build_manifest(
    *,
    asset_name: Optional[str] = None,
    asset_path: Optional[str] = None,
    asset_id: Optional[int] = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "asset": _compact_dict(
            {
                "name": asset_name,
                "path": asset_path,
                "id": asset_id,
            }
        ),
        "dcc": {},
    }


def _ensure_manifest_base(manifest: dict[str, Any]) -> dict[str, Any]:
    manifest.setdefault("schema_version", 1)
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
    temp_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp_path, manifest_path)


def _parse_version_from_name(stem: str, ext: str, name: str) -> Optional[int]:
    pattern = _VERSION_RE_TEMPLATE.format(stem=re.escape(stem), ext=re.escape(ext))
    match = re.match(pattern, name)
    if not match:
        return None
    try:
        return int(match.group("ver"))
    except Exception:
        return None


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
    """Copy a file into .backup using the next version number.

    Returns the new backup path or None if the source does not exist.
    """
    if ensure_exists and not source_path.exists():
        log.warning("Backup skipped; source missing: %s", source_path)
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)

    resolved_stem = stem or source_path.stem
    resolved_ext = (ext or source_path.suffix.lstrip(".")) or "dat"

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
    note: Optional[str] = None,
    tool_version: Optional[str] = None,
    asset_name: Optional[str] = None,
    asset_path: Optional[str] = None,
    asset_id: Optional[int] = None,
    variant: Optional[str] = None,
    publish_path: Optional[Path] = None,
    extra: Optional[dict[str, Any]] = None,
) -> Optional[BackupResult]:
    """Copy a file into .backup only if the signature changed.

    Records a publish entry in the manifest with signature + metadata.
    Returns None if the source is missing and ensure_exists is True.
    """
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
        note=note,
        tool_version=tool_version,
        extra=extra_payload,
        asset_name=asset_name,
        asset_path=asset_path,
        asset_id=asset_id,
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
    note: Optional[str] = None,
    tool_version: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
    asset_name: Optional[str] = None,
    asset_path: Optional[str] = None,
    asset_id: Optional[int] = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    _ensure_manifest_base(manifest)

    if asset_name or asset_path or asset_id is not None:
        manifest["asset"].update(
            _compact_dict({"name": asset_name, "path": asset_path, "id": asset_id})
        )

    entry = _compact_dict(
        {
            "timestamp": _utc_now_iso(),
            "user": user or _safe_user(),
            "host": host or _safe_host(),
            "source_file": str(source_path) if source_path else None,
            "backup_file": str(backup_path) if backup_path else None,
            "version": version,
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


__all__ = [
    "backup_file",
    "backup_if_changed",
    "compute_signature",
    "BackupResult",
    "build_manifest",
    "get_manifest_path",
    "list_versions",
    "load_manifest",
    "next_version",
    "record_publish",
    "save_manifest",
    "versioned_filename",
]
