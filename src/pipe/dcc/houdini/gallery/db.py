"""Writes to an Asset Gallery SQLite DB through Houdini's own data source."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator, cast

import hou

from . import db_lock


class Transaction:
    """The items of one DB, editable until the enclosing `transaction` ends."""

    def __init__(self, source: hou.AssetGalleryDataSource) -> None:
        self._source = source

    def files(self) -> dict[str, Path]:
        """Item id to the file it points at, for every visible item."""
        return {
            item_id: Path(self._source.filePath(item_id)) for item_id in self._ids()
        }

    def upsert(self, *, label: str, file_path: Path, thumbnail: bytes | None) -> str:
        """Make the DB hold exactly one item for `file_path` and return its id.

        An existing item keeps its id (and its thumbnail when none is given);
        extra items for the same file are dropped.
        """
        source = self._source
        existing = [
            item_id for item_id, file in self.files().items() if file == file_path
        ]
        if not existing:
            item_id = source.addItem(
                label, file_path=str(file_path), thumbnail=thumbnail or b""
            )
            if not item_id:
                raise RuntimeError(
                    f"Asset Gallery refused to add {label} ({file_path})"
                )
            source.setOwnsFile(item_id, False)
            return item_id

        item_id, *duplicates = existing
        if duplicates:
            source.markItemsForDeletion(tuple(duplicates))
        source.setLabel(item_id, label)
        source.setModificationDate(item_id, int(time.time()))
        if thumbnail:
            # The stub declares a str; the API takes PNG bytes.
            cast(Any, source).setThumbnail(item_id, thumbnail)
        return item_id

    def prune(self, keep_files: set[Path]) -> list[tuple[str, Path]]:
        """Drop every item whose file is not in `keep_files`; return what went."""
        stale = {
            item_id: file
            for item_id, file in self.files().items()
            if file not in keep_files
        }
        if stale:
            self._source.markItemsForDeletion(tuple(stale))
        return [(self._source.label(item_id), file) for item_id, file in stale.items()]

    def _ids(self) -> list[str]:
        # Missing from the hou stubs.
        return list(cast(Any, self._source).itemIds())


@contextmanager
def transaction(db_path: Path) -> Iterator[Transaction]:
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


def upsert_item(
    db_path: Path, *, label: str, file_path: Path, thumbnail: bytes | None
) -> str:
    with transaction(db_path) as tx:
        return tx.upsert(label=label, file_path=file_path, thumbnail=thumbnail)


def files(db_path: Path) -> dict[str, Path]:
    """What `Transaction.files` would return, without opening a write."""
    if not db_path.is_file():
        return {}
    with db_lock(db_path):
        return Transaction(hou.AssetGalleryDataSource(str(db_path))).files()


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


__all__ = ["Transaction", "files", "transaction", "upsert_item"]
