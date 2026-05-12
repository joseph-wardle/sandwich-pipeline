import logging
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from maya import cmds
from core.util.paths import get_rig_build_path

from .progress import RigBuildProgressManager

log = logging.getLogger(__name__)


@dataclass
class RigDefinition:
    name: str
    type: str
    variant: str | None = None


def has_local_override_directory(
    rig: RigDefinition,
    local_override: Path | None = None,
) -> bool:
    if local_override is not None:
        override_asset_root = local_override / rig.type / rig.name
        if override_asset_root.exists():
            return True
    return False


def resolve_rig_build_asset_root(
    rig: RigDefinition,
    local_override: Path | None = None,
) -> Path:
    if local_override is not None:
        override_asset_root = local_override / rig.type / rig.name
        if override_asset_root.exists():
            return override_asset_root
    asset_root = get_rig_build_path() / rig.type / rig.name
    return asset_root


@contextmanager
def redirect_external_logger(
    external_logger: logging.Logger, target_logger: logging.Logger
):
    """Temporarily hooks an external logger into a specified target."""

    # Store original state
    original_parent = external_logger.parent
    original_propagate = external_logger.propagate

    try:
        external_logger.parent = target_logger
        external_logger.propagate = True
        yield external_logger
    finally:
        # Restore original state exactly as it was
        external_logger.parent = original_parent
        external_logger.propagate = original_propagate


class RigBuilder:
    def __init__(self) -> None:
        self._progress_slot: Callable[[float], None] | None = None
        pass

    def connect_progress(self, progress_slot: Callable[[float], None]):
        """Stores the slot (e.g., progress_bar.update_progress) to connect later."""
        self._progress_slot = progress_slot

    def build_rig(
        self,
        rig: RigDefinition,
        dev_build: bool = False,
        build_scope: str | None = None,
        override_directory: Path | None = None,
    ) -> bool:
        """
        This function is meant to call the rig build of an external rigging library (currently y-rig).
        However I hope that it is easy enough to change that if needed the underlying rig build system
        could be replaced without any trouble.

        It should return a bool: True if the rig built successfully and False if it failed or was cancelled.
        """
        from yrig.build import build_from_path

        # Grab the external logger
        build_logger = logging.getLogger("yrig")

        # Get paths.
        rig_build_path = resolve_rig_build_asset_root(rig, override_directory)
        guide_path = rig_build_path / "data/guide.sgt"

        if not guide_path.exists():
            error_message = f"Couldn't find the build data for {rig.name}. The build file should be located at {guide_path}"
            log.error(error_message)
            raise FileNotFoundError(error_message)

        progress_manager = RigBuildProgressManager()
        if self._progress_slot is not None:
            progress_manager.progress_changed.connect(self._progress_slot)
        with redirect_external_logger(build_logger, log):
            cmds.file(newFile=True, force=True)
            build_result = build_from_path(
                rig_root_path=rig_build_path,
                dev_build=dev_build,
                build_scope=build_scope,
                progress_callback=progress_manager.update_progress_with_step,
            )
        if build_result is False:
            return False  # Early return to avoid finishing the progress bar
        progress_manager.update_progress_with_step(1)
        return build_result
