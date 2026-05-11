from __future__ import annotations

import datetime
import re
from pathlib import Path

from Qt import QtCore, QtGui, QtWidgets

from core.versioning import VersionRecord, version_label

_VERSIONED_FILENAME_RE = re.compile(r"^(?P<stem>.+)\.v(?P<ver>\d+)\.(?P<ext>[^.]+)$")
_UNTITLED_LABEL = "(untitled)"


class VersionBrowserWidget(QtWidgets.QDialog):
    ACTION_OPEN = "open"
    ACTION_PROMOTE = "promote"

    _records: list[VersionRecord]
    _selected_action: str | None
    _owner_label: str
    _stream_label: str
    _current_version: int | None
    _table: QtWidgets.QTableWidget
    _detail_label: QtWidgets.QLabel
    _open_btn: QtWidgets.QPushButton
    _promote_btn: QtWidgets.QPushButton

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        records: list[VersionRecord],
        *,
        owner_label: str,
        stream_label: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._records = list(records)
        self._selected_action = None
        self._owner_label = (owner_label or "").strip() or "Item"
        self._stream_label = (stream_label or "").strip() or _detect_stream_label(
            self._records
        )
        self._current_version = _current_version(self._records)

        self.setParent(parent)
        self.setWindowTitle("Version History")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.resize(860, 540)

        self._build_ui()
        self._populate_table()
        self._on_selection_changed()

    def get_selected_action(self) -> str | None:
        return self._selected_action

    def get_selected_record(self) -> VersionRecord | None:
        row = self._selected_row()
        if row is None:
            return None
        if row < 0 or row >= len(self._records):
            return None
        return self._records[row]

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QtWidgets.QLabel(f"Version History - {self._stream_label}")
        header_font = header.font()
        header_font.setBold(True)
        header_font.setPointSize(header_font.pointSize() + 1)
        header.setFont(header_font)
        layout.addWidget(header)

        owner_info = QtWidgets.QLabel(f"Item: {self._owner_label}")
        owner_info.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(owner_info)

        self._table = QtWidgets.QTableWidget(self)
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["Version", "Title", "By", "Date", "Context"]
        )
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setWordWrap(False)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.itemDoubleClicked.connect(self._on_row_double_clicked)

        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header_view.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        layout.addWidget(self._table, 1)

        self._detail_label = QtWidgets.QLabel("")
        self._detail_label.setWordWrap(True)
        self._detail_label.setTextFormat(QtCore.Qt.PlainText)
        self._detail_label.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(self._detail_label)

        button_row = QtWidgets.QHBoxLayout()
        self._open_btn = QtWidgets.QPushButton("Open Version")
        self._promote_btn = QtWidgets.QPushButton("Save as New Version")
        cancel_btn = QtWidgets.QPushButton("Cancel")
        self._open_btn.clicked.connect(self._accept_open)
        self._promote_btn.clicked.connect(self._accept_promote)
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(self._open_btn)
        button_row.addWidget(self._promote_btn)
        button_row.addStretch(1)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)

    def _populate_table(self) -> None:
        self._table.setRowCount(len(self._records))
        for row, record in enumerate(self._records):
            version_text = version_label(record.version)
            title_text = (record.title or "").strip() or _UNTITLED_LABEL
            user_text = (record.user or "").strip() or "-"
            date_text = _format_timestamp(record.timestamp)
            context_text = (record.context or "").strip() or "-"

            version_item = _table_item(version_text)
            title_item = _table_item(title_text)
            user_item = _table_item(user_text)
            date_item = _table_item(date_text)
            context_item = _table_item(context_text)

            self._table.setItem(row, 0, version_item)
            self._table.setItem(row, 1, title_item)
            self._table.setItem(row, 2, user_item)
            self._table.setItem(row, 3, date_item)
            self._table.setItem(row, 4, context_item)

            if not (record.title or "").strip():
                _dim_item(title_item)

            backup_missing = record.backup_path is not None and not _has_backup_file(
                record
            )
            if backup_missing:
                for col in range(self._table.columnCount()):
                    item = self._table.item(row, col)
                    if item is not None:
                        _dim_item(item)
                        item.setToolTip("Backup file not found on disk")

            if _is_current_row(row, record, self._records, self._current_version):
                _set_row_bold(self._table, row, True)
                if not backup_missing:
                    for col in range(self._table.columnCount()):
                        item = self._table.item(row, col)
                        if item is not None:
                            item.setToolTip("Current version")

        if self._records:
            self._table.selectRow(0)

    def _selected_row(self) -> int | None:
        selected_indexes = self._table.selectionModel().selectedRows()
        if not selected_indexes:
            return None
        return selected_indexes[0].row()

    def _on_selection_changed(self) -> None:
        record = self.get_selected_record()
        row = self._selected_row()
        if record is None or row is None:
            self._detail_label.setText("Select a version to see details.")
            self._open_btn.setEnabled(False)
            self._promote_btn.setEnabled(False)
            return

        note_text = (record.note or "").strip() or "(none)"
        has_backup_path = _has_backup_file(record)
        if record.backup_path is None:
            self._detail_label.setText(
                f"Note: {note_text}\nBackup file: (not recorded)"
            )
        elif not has_backup_path:
            self._detail_label.setText(
                f"Note: {note_text}\nBackup file not found on disk."
            )
        else:
            self._detail_label.setText(f"Note: {note_text}")

        self._open_btn.setEnabled(has_backup_path)
        self._promote_btn.setEnabled(
            has_backup_path
            and not _is_current_row(row, record, self._records, self._current_version)
        )

    def _on_row_double_clicked(self, _item: QtWidgets.QTableWidgetItem) -> None:
        if self._open_btn.isEnabled():
            self._accept_open()

    def _accept_open(self) -> None:
        if self.get_selected_record() is None or not self._open_btn.isEnabled():
            return
        self._selected_action = self.ACTION_OPEN
        self.accept()

    def _accept_promote(self) -> None:
        record = self.get_selected_record()
        row = self._selected_row()
        if record is None or row is None:
            return
        if (
            _is_current_row(row, record, self._records, self._current_version)
            or not self._promote_btn.isEnabled()
        ):
            return
        self._selected_action = self.ACTION_PROMOTE
        self.accept()


def _table_item(text: str) -> QtWidgets.QTableWidgetItem:
    item = QtWidgets.QTableWidgetItem(text)
    item.setFlags(QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)
    return item


def _set_row_bold(table: QtWidgets.QTableWidget, row: int, bold: bool) -> None:
    for col in range(table.columnCount()):
        item = table.item(row, col)
        if item is None:
            continue
        font = item.font()
        font.setBold(bold)
        item.setFont(font)


def _dim_item(item: QtWidgets.QTableWidgetItem) -> None:
    item.setForeground(QtGui.QBrush(QtGui.QColor(130, 130, 130)))


def _has_backup_file(record: VersionRecord) -> bool:
    backup_path = record.backup_path
    if not (backup_path and backup_path.exists() and backup_path.is_file()):
        return False

    backup_root = record.backup_root
    if backup_root is None or not record.backup_members:
        return True

    resolved_backup_root = Path(backup_root)
    return all(
        (resolved_backup_root / member_path).exists()
        for member_path in record.backup_members
    )


def _format_timestamp(timestamp: str | None) -> str:
    if not timestamp:
        return "-"
    text = str(timestamp).strip()
    if not text:
        return "-"
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return text
    return parsed.strftime("%b %d %H:%M")


def _current_version(records: list[VersionRecord]) -> int | None:
    versions = [record.version for record in records if record.version is not None]
    return max(versions) if versions else None


def _is_current_row(
    row: int,
    record: VersionRecord,
    records: list[VersionRecord],
    current_version: int | None,
) -> bool:
    if current_version is not None:
        return record.version == current_version
    return row == 0 and bool(records)


def _detect_stream_label(records: list[VersionRecord]) -> str:
    for record in records:
        backup_path = record.backup_path
        if backup_path is None:
            continue
        match = _VERSIONED_FILENAME_RE.match(backup_path.name)
        if match:
            return f"{match.group('stem')}.{match.group('ext')}"

    for record in records:
        if not record.source_file:
            continue
        source_name = Path(record.source_file).name
        if source_name:
            return source_name

    return "versioned file"


__all__ = ["VersionBrowserWidget"]
