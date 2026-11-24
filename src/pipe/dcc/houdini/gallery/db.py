"""Writes to an Asset Gallery SQLite DB through Houdini's own data source."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Mapping
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Generator, cast

import hou

from . import db_lock
from .items import GalleryItem, ItemKey, item_key


class Transaction:
    """The items of one DB, editable until the enclosing `transaction` ends."""

    def __init__(self, source: hou.AssetGalleryDataSource) -> None:
        self._source = source

    def keys(self) -> dict[str, ItemKey]:
        """Item id to the asset and variant it lists, for every visible item."""
        source = self._source
        return {
            item_id: item_key(source.filePath(item_id), source.blindData(item_id))
            for item_id in self._ids()
        }

    def upsert(self, item: GalleryItem, thumbnail: bytes | None) -> str:
        """Make the DB hold exactly one entry for `item` and return its id."""
        source = self._source
        existing = [item_id for item_id, key in self.keys().items() if key == item.key]
        if not existing:
            item_id = source.addItem(
                item.label,
                file_path=item.file_path,
                thumbnail=thumbnail or b"",
                blind_data=item.blind_data,
            )
            if not item_id:
                raise RuntimeError(
                    f"Asset Gallery refused to add {item.label} ({item.file_path})"
                )
            source.setOwnsFile(item_id, False)
            return item_id

        item_id, *duplicates = existing
        if duplicates:
            source.markItemsForDeletion(tuple(duplicates))
        source.setLabel(item_id, item.label)
        source.setFilePath(item_id, item.file_path)
        # The stub declares a str; the API takes bytes.
        cast(Any, source).setBlindData(item_id, item.blind_data)
        source.setModificationDate(item_id, int(time.time()))
        if thumbnail:
            # The stub declares a str; the API takes PNG bytes.
            cast(Any, source).setThumbnail(item_id, thumbnail)
        return item_id

    def prune(
        self, keep: set[ItemKey], *, within: set[str] | None = None
    ) -> list[tuple[str, str]]:
        """Drop every entry not in `keep`; return the label and file of each."""
        stale = [
            item_id
            for item_id, key in self.keys().items()
            if key not in keep and (within is None or key[0] in within)
        ]
        if stale:
            self._source.markItemsForDeletion(tuple(stale))
        return [
            (self._source.label(item_id), self._source.filePath(item_id))
            for item_id in stale
        ]

    def _ids(self) -> list[str]:
        # Missing from the hou stubs.
        return list(cast(Any, self._source).itemIds())


@contextmanager
def transaction(db_path: Path) -> Generator[Transaction]:
    """One locked write to `db_path`, creating the DB if it does not exist."""
    with db_lock(db_path):
        source = hou.AssetGalleryDataSource(str(db_path))
        if not source.isValid() or source.isReadOnly():
            raise RuntimeError(f"Asset Gallery DB is not writable: {db_path}")
        source.startTransaction()
        try:
            yield Transaction(source)
        finally:
            source.endTransaction(commit=True)
            _purge_and_checkpoint(db_path)


def sync_asset(
    db_path: Path, thumbnails: Mapping[GalleryItem, bytes | None]
) -> list[str]:
    """Make the DB list exactly these items for their asset; return their ids."""
    with transaction(db_path) as tx:
        ids = [tx.upsert(item, thumbnail) for item, thumbnail in thumbnails.items()]
        keys = {item.key for item in thumbnails}
        tx.prune(keys, within={key[0] for key in keys})
        return ids


def keys(db_path: Path) -> dict[str, ItemKey]:
    """What `Transaction.keys` would return, without opening a write."""
    if not db_path.is_file():
        return {}
    with db_lock(db_path):
        return Transaction(hou.AssetGalleryDataSource(str(db_path))).keys()


def _purge_and_checkpoint(db_path: Path) -> None:
    """The SQLite clean-up Houdini leaves to us after a transaction."""
    marked = "SELECT id FROM items WHERE marked_for_deletion = 1"
    with closing(sqlite3.connect(db_path)) as connection:
        with connection:
            # markItemsForDeletion only hides rows; Houdini deletes them when
            # the data source object dies, which is not a moment we control.
            connection.execute(f"DELETE FROM item_tags WHERE item_id IN ({marked})")
            connection.execute(f"DELETE FROM item_metadata WHERE item_id IN ({marked})")
            connection.execute("DELETE FROM items WHERE marked_for_deletion = 1")
            # Houdini stores a path under the DB's own directory as "./…",
            # which a copy of the DB in $TMPDIR would resolve against $TMPDIR.
            connection.execute(
                "UPDATE items SET item_file = ? || substr(item_file, 3) "
                "WHERE item_file LIKE './%'",
                (f"{db_path.parent}/",),
            )
        # Fold the write-ahead log into the DB file so a plain copy is complete.
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


__all__ = ["Transaction", "keys", "sync_asset", "transaction"]
