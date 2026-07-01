from __future__ import annotations
from typing import Callable, Sequence

from Qt.QtCore import QObject, Signal

from .test import RigBuildTest


class ProgressStep:
    def __init__(self, name: str, weight: float = 1):
        self.name = name
        self._weight = weight
        self._progress: float = 0.0
        self._child_steps: list[ProgressStep] = []
        self._parent: ProgressStep | None = None
        self._child_weight_sum: float = 0
        self._finished: bool = False
        self._on_progress: Callable[[float], None] | None = None

    def get_progress(self):
        return self._progress

    def connect_progress(self, callback: Callable[[float], None] | None = None):
        self._on_progress = callback

    def add_child_step(self, step: ProgressStep):
        step._parent = self
        self._child_steps.append(step)
        self._child_weight_sum += step._weight

    def get_child_steps(self) -> list[ProgressStep]:
        return self._child_steps

    def _update_progress_from_children(self):
        if all(child._finished for child in self._child_steps):
            self._set_finished()
            return

        cumulative_progress = 0
        for child in self._child_steps:
            child_progress = child.get_progress()
            scaled_progress = child_progress * (child._weight / self._child_weight_sum)
            cumulative_progress += scaled_progress
        self._progress = cumulative_progress
        self._propogate_progress()

    def _propogate_progress(self):
        if self._parent is not None:
            self._parent._update_progress_from_children()
        if self._on_progress is not None:
            try:
                self._on_progress(self._progress)
            except Exception:
                pass

    def update_progress(self, progress: float):
        if self._child_steps:
            return
        if progress == 1:
            self.finish_step()
            return
        self._progress = progress
        self._propogate_progress()

    def _set_finished(self):
        self._finished = True
        self._progress = 1

    def finish_step(self):
        if self._finished:
            return
        for child in self._child_steps:
            child.finish_step()
        self._set_finished()
        self._propogate_progress()


class ProgressManager(QObject):
    progress_changed: Signal = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self._progress: float = 0

    def reset_progress(self) -> None:
        self._progress = 0
        self.progress_changed.emit(self._progress)

    def get_progress(self) -> float:
        """Gives a progress between 0 and 1"""
        return self._progress

    def update_progress(self) -> None:
        self.progress_changed.emit(self._progress)

    def update_progress_finished(self) -> None:
        self._progress = 1
        self.progress_changed.emit(self._progress)


class RigBuildProgressManager(ProgressManager):
    def __init__(
        self,
    ):
        super().__init__()
        self.reset_progress()

    def update_progress_with_step(self, progress: float, step_name: str | None = None):
        self._progress = progress
        self.update_progress()


class TestProgressManager(ProgressManager):
    def __init__(
        self,
        tests: Sequence[RigBuildTest],
    ):
        super().__init__()
        self._total_tests: int = len(tests)
        self.reset_progress()

    def update_progress_from_test_run(self, test: RigBuildTest, passed: bool):
        self._progress += 1 / self._total_tests
        self.update_progress()
