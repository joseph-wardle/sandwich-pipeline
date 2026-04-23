import inspect
from typing import Callable, Sequence

from Qt import QtCore
from Qt.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel
from Qt.QtWidgets import QApplication, QHBoxLayout, QListView, QPushButton, QWidget

from ..progress import TestProgressManager
from ..test import RIG_BUILD_TESTS, RigBuildTest, TestRunner

PASSED_COLOR = QColor(0, 94, 75)
FAILED_COLOR = QColor(130, 42, 50)


class TestSelectListModel(QStandardItemModel):
    def __init__(self, parent: QtCore.QObject | None):
        super().__init__(parent=parent)
        self.itemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, item: QStandardItem):
        if isinstance(item, TestItem):
            if item.checkState() == QtCore.Qt.CheckState.Checked:
                item.enable_test()
            else:
                item.disable_test()


class TestItem(QStandardItem):
    def __init__(self, test: RigBuildTest):
        super().__init__(test.name)
        self.test = test
        self.test_enabled: bool = True
        self.test_passed: bool | None = None
        self.setEditable(False)
        self.setSelectable(False)
        self.setCheckable(True)
        self.setCheckState(QtCore.Qt.CheckState.Checked)
        docstring = inspect.getdoc(test)
        if docstring:
            self.setToolTip(docstring)

    def run(self):
        result = self.test.run()
        self.update_status(result)

    def update_status(self, passed: bool):
        self.test_passed = passed
        if passed:
            self.setBackground(QBrush(PASSED_COLOR))
        else:
            self.setBackground(QBrush(FAILED_COLOR))

    def clear_status(self):
        self.test_passed = None
        self.setBackground(QBrush())

    def is_enabled(self) -> bool:
        return self.test_enabled

    def enable_test(self):
        self.test_enabled = True

    def disable_test(self):
        self.test_enabled = False


class TestSelectList(QListView):
    def __init__(self) -> None:
        super().__init__()
        self._progress_manager: TestProgressManager | None = None
        self._progress_slot = None
        self.setup_ui()

    def setup_ui(self):
        self.setResizeMode(QListView.Adjust)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setMinimumSize(32, 22)
        self.item_model = TestSelectListModel(self)
        self.setModel(self.item_model)
        self.setSelectionMode(QListView.SingleSelection)
        self.setSpacing(2)

        # Overlay buttons in the bottom-right corner of the viewport
        self._button_overlay = QWidget(self.viewport())
        overlay_layout = QHBoxLayout(self._button_overlay)
        overlay_layout.setContentsMargins(0, 0, 4, 4)
        overlay_layout.setSpacing(4)

        self.enable_tests_button = QPushButton("All On")
        self.enable_tests_button.setMaximumHeight(16)
        self.enable_tests_button.clicked.connect(self.enable_all_tests)
        overlay_layout.addWidget(self.enable_tests_button)

        self.disable_tests_button = QPushButton("All Off")
        self.disable_tests_button.setMaximumHeight(16)
        self.disable_tests_button.clicked.connect(self.disable_all_tests)
        overlay_layout.addWidget(self.disable_tests_button)
        self.verticalScrollBar().valueChanged.connect(self._reposition_overlay)
        self.horizontalScrollBar().valueChanged.connect(self._reposition_overlay)

        self._button_overlay.adjustSize()

        # Populate tests.
        self.test_items: list[TestItem] = []
        self.populate_tests([test() for test in RIG_BUILD_TESTS])

    def populate_tests(self, tests: Sequence[RigBuildTest]) -> None:
        for test in tests:
            self.add_item(test)

    def add_item(self, test: RigBuildTest) -> None:
        item = TestItem(test)
        item.setCheckState(QtCore.Qt.CheckState.Checked)
        self.item_model.appendRow(item)
        self.test_items.append(item)

    def clear_test_status(self) -> None:
        self._set_border_color(None)
        for test_item in self.test_items:
            test_item.clear_status()

    def run_tests(self, selected_only: bool = True) -> None:
        self.clear_test_status()
        QApplication.processEvents()

        enabled_tests = [
            test_item.test for test_item in self.test_items if test_item.is_enabled()
        ]
        self._progress_manager = TestProgressManager(
            enabled_tests,
        )
        if self._progress_slot is not None:
            self._progress_manager.progress_changed.connect(self._progress_slot)

        test_runner = TestRunner(
            enabled_tests,
            test_run_callback=self._on_test_finished_local,
        )
        test_runner.run_tests()
        self._progress_manager.update_progress_finished()
        self._progress_manager = None

    def _run_tests_internal(self):
        enabled_tests = [
            test_item.test for test_item in self.test_items if test_item.is_enabled()
        ]
        self._progress_manager = TestProgressManager(
            enabled_tests,
        )
        if self._progress_slot is not None:
            self._progress_manager.progress_changed.connect(self._progress_slot)

        test_runner = TestRunner(
            enabled_tests,
            test_run_callback=self._on_test_finished_local,
        )
        test_runner.run_tests()
        self._progress_manager.update_progress_finished()
        self._progress_manager = None

    def _update_overall_status(self) -> None:
        enabled_items = [i for i in self.test_items if i.is_enabled()]

        if not enabled_items:
            self._set_border_color(None)

        if all(test.test_passed is True for test in enabled_items):
            self._set_border_color(PASSED_COLOR)
        elif any(test.test_passed is False for test in enabled_items):
            self._set_border_color(FAILED_COLOR)  # red
        else:
            self._set_border_color(None)

    def on_test_finished(self, test: RigBuildTest, passed: bool) -> None:
        for item in self.test_items:
            if type(item.test) is type(test):
                item.update_status(passed)
                if not passed:
                    self.scrollTo(item.index(), QListView.ScrollHint.EnsureVisible)
                self._update_overall_status()
                QApplication.processEvents()
                break

    def _on_test_finished_local(self, test: RigBuildTest, passed: bool) -> None:
        self.on_test_finished(test, passed)
        if self._progress_manager is not None:
            self._progress_manager.update_progress_from_test_run(test, passed)

    def enable_all_tests(self) -> None:
        for test_item in self.test_items:
            test_item.setCheckState(QtCore.Qt.CheckState.Checked)

    def disable_all_tests(self) -> None:
        for test_item in self.test_items:
            test_item.setCheckState(QtCore.Qt.CheckState.Unchecked)

    def connect_progress(self, progress_slot: Callable[[float], None]) -> None:
        """Stores the slot (e.g., progress_bar.update_progress) to connect later."""
        self._progress_slot = progress_slot

    def _set_border_color(self, color: QColor | None = None) -> None:
        if color is not None:
            self.setStyleSheet(f"""
                QListView {{
                    border: 2px solid rgba{color.getRgb()};
                    border-radius: 3px;
                }}
            """)
        else:
            self.setStyleSheet("")

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._reposition_overlay()

    def _reposition_overlay(self) -> None:
        overlay = self._button_overlay
        viewport = self.viewport()
        overlay.adjustSize()
        x = viewport.width() - overlay.width()
        y = viewport.height() - overlay.height()
        overlay.move(x, y)
        overlay.raise_()
