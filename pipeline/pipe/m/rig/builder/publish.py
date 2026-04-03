import logging
from typing import Callable, Iterable

from env_sg import DB_Config
from maya import cmds

from pipe.asset.paths import paths_for_asset
from pipe.db import DB
from pipe.versioning.store import next_version, versioned_filename

from .build import RigBuilder
from .progress import ProgressStep, TestProgressManager
from .test import RIG_BUILD_TESTS, RigBuildTest, TestRunner

log = logging.getLogger(__name__)


class RigPublisher:
    def __init__(self) -> None:
        self._conn = DB.Get(DB_Config)

        self.root_progress = ProgressStep("Build, Publish and Test")
        self.build_progress = ProgressStep("Rig Build", 15)
        self.root_progress.add_child_step(self.build_progress)
        self.test_progress = ProgressStep("Rig Test", 1)
        self.root_progress.add_child_step(self.test_progress)
        self.publish_progress = ProgressStep("Rig Publish", 2)
        self.root_progress.add_child_step(self.publish_progress)
        self.test_progress_manager: TestProgressManager | None = None
        self._test_view_update_callback: Callable[[RigBuildTest, bool], None] | None = (
            None
        )

    def connect_progress(self, progress_slot: Callable[[float], None]):
        """Stores the slot (e.g., progress_bar.update_progress) to connect later."""
        self.root_progress.connect_progress(progress_slot)

    def connect_test_view(
        self, test_view_update_callback: Callable[[RigBuildTest, bool], None]
    ):
        self._test_view_update_callback = test_view_update_callback

    def _on_test_run(self, test: RigBuildTest, passed: bool):
        if self.test_progress_manager is not None:
            self.test_progress_manager.update_progress_from_test_run(test, passed)
        if self._test_view_update_callback is not None:
            self._test_view_update_callback(test, passed)

    def _build_rig(self, rig_name: str, rig_type: str) -> bool:
        rig_builder = RigBuilder()
        rig_builder.connect_progress(self.build_progress.update_progress)
        rig_build_result = rig_builder.build_rig(rig_name, rig_type)
        return rig_build_result

    def _run_tests(self, tests: Iterable[type[RigBuildTest]]) -> bool:
        self.build_progress.finish_step()
        test_objects = [test() for test in tests]
        self.test_progress_manager = TestProgressManager(
            test_objects,
        )
        self.test_progress_manager.progress_changed.connect(
            self.test_progress.update_progress
        )
        test_runner = TestRunner(test_objects, self._on_test_run)
        return test_runner.run_tests()

    def _publish_rig_model(self, rig_name: str):
        from pipe.m.publish.usdchaser.export import ExportChaser, ExportChaserMode

        publish_asset = self._conn.get_asset_by_name(rig_name)
        publish_asset_paths = paths_for_asset(publish_asset)
        rig_model_publish_path = publish_asset_paths.rig_path / "usd/geo.usd"
        cmds.mayaUSDExport(  # type: ignore
            chaser=[ExportChaser.ID],
            file=str(rig_model_publish_path),
            chaserArgs=[(ExportChaser.ID, "mode", ExportChaserMode.RIG)],
            exportCollectionBasedBindings=True,
            exportMaterialCollections=True,
            legacyMaterialScope=True,
            materialCollectionsPath="/rig/geo",
            shadingMode="useRegistry",
        )
        log.info(
            f"PUBLISH: {rig_name} rig model USD published to {rig_model_publish_path}"
        )

    def _publish_rig(self, rig_name: str) -> bool:
        publish_asset = self._conn.get_asset_by_name(rig_name)
        publish_asset_paths = paths_for_asset(publish_asset)
        rig_publish_path = publish_asset_paths.rig_path
        rig_versions_path = publish_asset_paths.rig_versions_path

        next_version_number = next_version(
            publish_asset_paths.rig_versions_path, stem=rig_name, ext="mb"
        )
        rig_version_filepath = rig_versions_path / versioned_filename(
            stem=rig_name, ext="mb", version=next_version_number
        )

        cmds.select("rig")
        cmds.file(str(rig_version_filepath), exportSelected=True, type="mayaBinary")
        log.info(
            f"PUBLISH: {rig_name} was successfully built and published to {rig_version_filepath}"
        )
        rig_publish_filepath = (rig_publish_path / rig_name).with_suffix(".mb")
        if rig_publish_filepath.is_symlink():
            rig_publish_filepath.unlink()
        elif rig_publish_filepath.exists():
            log.error(
                f"PUBLISH: The file at {rig_publish_filepath} already exists and is not a symlink! To avoid data loss symlink creation/updating was cancelled."
            )
            return False
        rig_publish_filepath.symlink_to(rig_version_filepath)
        log.info(f"PUBLISH: {rig_name} rig symlink updated to {rig_version_filepath}")
        self.publish_progress.finish_step()
        return True

    def build_test_and_publish(self, rig_name: str, rig_type: str):
        build_complete = self._build_rig(rig_name, rig_type)
        if not build_complete:
            log.error(f"{rig_name} failed to build properly and wasn't published!")
            return
        tests_passed = self._run_tests(RIG_BUILD_TESTS)
        if not tests_passed:
            log.error(
                f"{rig_name} failed one or more required tests and wasn't published!"
            )
            return
        else:
            self._publish_rig(rig_name)
            self._publish_rig_model(rig_name)
