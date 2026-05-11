from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Callable, Iterable

log = logging.getLogger(__name__)
test_log = logging.getLogger("dcc.maya.rig.builder.test")


class TestRunner:
    def __init__(
        self,
        tests: Iterable[RigBuildTest],
        test_run_callback: Callable[[RigBuildTest, bool], None] | None = None,
    ) -> None:
        self.tests = tests
        self._test_run_callback = test_run_callback

    def run_tests(self) -> bool:
        """Runs all of the TestRunner's tests and returns True if all tests passed."""
        passing: bool = True
        for test in self.tests:
            try:
                test_passed = test.run()
            except Exception as e:
                test_log.error(f"{test.name}: {e}", exc_info=e)
                test_passed = False
            if self._test_run_callback is not None:
                self._test_run_callback(test, test_passed)
            if not test_passed:
                passing = False
        return passing


class RigBuildTest(ABC):
    def __init__(self, name: str = "Test"):
        self.name = name
        pass

    @abstractmethod
    def run(self) -> bool:
        """Should be implemented in all tests, returns True if the test passed."""
        pass

    def log_warn(self, message: str):
        test_log.warning(f"{self.name}: {message}")

    def log_success(self):
        test_log.info(f"{self.name}: PASSED")
