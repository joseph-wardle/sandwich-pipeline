from __future__ import annotations

import logging
import os
import platform
import shutil
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import maya.cmds as mc
from env_sg import DB_Config

from dcc.maya import runtime as maya_runtime
from core import telemetry
from core.ui.dialogs import FilteredListDialog, MessageDialog
from dcc.maya.util.selection import maintain_selection
from core.shotgrid import Asset, SGEntity, Shot, ShotGrid

if TYPE_CHECKING:
    from Qt.QtWidgets import QWidget

log = logging.getLogger(__name__)


class USDExportError(Exception):
    """`error_code` is read by `telemetry.record()`"""

    error_code = "USD_EXPORT_FAILED"


class PublishCopyError(Exception):
    """Raised when copying a published file into its final publish location fails."""

    error_code = "PUBLISH_COPY_FAILED"


class Publisher:
    """Class for publishing USDs out of Maya"""

    _conn: ShotGrid
    _dialog: FilteredListDialog
    _dialog_T: type[FilteredListDialog]
    _entity: SGEntity
    _publish_path: Path
    _selected_item: str
    _system: str
    _use_sg_entity: bool
    _window: QWidget | None

    def __init__(
        self, dialog: type[FilteredListDialog] | None = None, use_sg_entity: bool = True
    ) -> None:
        self._conn = ShotGrid.connect(DB_Config)
        self._window = maya_runtime.get_main_qt_window()
        self._system = platform.system()
        self._dialog_T = dialog or FilteredListDialog
        self._use_sg_entity = use_sg_entity

    @staticmethod
    def _assert_not_none(fun):
        @wraps(fun)
        def wrap(*args, **kwargs):
            result = fun(*args, **kwargs)
            if result is None:
                raise AssertionError
            return result

        return wrap

    def __init_subclass__(cls, *args, **kwargs) -> None:
        """Wrap overridden definitions of these methods"""
        super().__init_subclass__(*args, **kwargs)
        funcs = (cls._get_entity_from_name, cls._get_save_path)
        for f in funcs:
            setattr(cls, f.__name__, cls._assert_not_none(f))

    @property
    def _IS_WINDOWS(self) -> bool:
        return self._system == "Windows"

    def _prepublish(self) -> bool:
        """Runs before any other part of the publish function"""
        return True

    def _get_entity_list(self) -> list[str]:
        """Get a list of strings to prompt in the dialog"""
        return []

    @_assert_not_none
    def _get_entity_from_name(self, display_name: str) -> SGEntity | None:
        """Turn the chosen display name into a SG entity"""
        return None

    @_assert_not_none
    def _get_save_path(self) -> Path | None:
        """Get the save path"""
        if user_select := mc.fileDialog2(fileFilter="*.usd"):
            return Path(user_select[0])
        return None

    def _presave(self) -> bool:
        """Run before any files are saved out"""
        return True

    def _get_mayausd_kwargs(self) -> dict[str, Any]:
        """A dictionary of additional arguments to `mc.mayaUSDExport`"""
        return {}

    def _get_confirm_message(self) -> str:
        return f"The selected objects have been exported to {self._publish_path}"

    # Subclasses opt in to `publish.usd` telemetry by setting this to a short
    # stable token (e.g. "asset", "anim", "camera"). The value becomes the
    # `kind` payload field on the emitted event, which is what the Grafana
    # dashboards group by. Leave as None (the default) for internal helpers
    # or one-off flows the show doesn't need to track
    _PUBLISH_KIND: str | None = None

    def _publish_kind(self) -> str | None:
        return self._PUBLISH_KIND

    def _publish_scope_kwargs(self) -> dict[str, object]:
        """Return entity kwargs for `telemetry.record()` describing this publish."""
        entity = getattr(self, "_entity", None)
        shot = getattr(self, "_shot", None)
        scene_asset = getattr(self, "_scene_asset", None)

        kwargs: dict[str, object] = {}

        asset_obj: object | None = scene_asset
        if asset_obj is None and isinstance(entity, Asset):
            asset_obj = entity
        if asset_obj is not None:
            kwargs["asset"] = asset_obj

        shot_obj: object | None = shot
        if shot_obj is None and isinstance(entity, Shot):
            shot_obj = entity
        if shot_obj is not None:
            kwargs["shot"] = shot_obj

        return kwargs

    def publish(self) -> None:
        """Generic publishing function.
        `Exporter().publish()` will publish the selected geometry to the place
        chosen in the pop-up dialog, accounting for the USD export bug on
        Windows. Specific functionality is defined by passing a
        `FilteredListDialog` class into `__init__` and by overriding the
        following functions:
          - `prepublish(self)`
          - `get_entity_list(self) -> list[str]`
          - `get_entity_from_name(self, display_name: str) -> SGEntity`
          - `get_save_path(self) -> Path`
          - `presave(self)`
          - `get_mayausd_kwargs(self) -> dict[str, Any]`
        """
        with maintain_selection():
            if not self._prepublish():
                return

            if entity_list := self._get_entity_list():
                if not self._select_publish_target_or_cancel(entity_list):
                    return

            self._publish_path = self._get_save_path()
            if not self._publish_path:
                mc.error("No save path found!")
                return

            if not self._presave():
                return

            self._do_publish_export()

            MessageDialog(
                self._window,
                self._get_confirm_message(),
                "Export Complete",
            ).exec_()

    def _select_publish_target_or_cancel(self, entity_list: list[str]) -> bool:
        """Run the entity-selection dialog and populate `self._selected_item`/`self._entity`.

        Returns True if an entity was successfully selected. Returns False if
        the user cancelled or the selection was invalid — in which case an
        artist-facing dialog has already been shown.
        """
        from dcc.maya.publish.asset import (
            PublishAssetOptionsDialog,
            PublishAssetPickerDialog,
        )
        from dcc.maya.publish.previs_asset import PublishPrevisAssetDialog

        dialog_type = cast(Any, self._dialog_T)
        if self._dialog_T in (
            PublishAssetOptionsDialog,
            PublishAssetPickerDialog,
            PublishPrevisAssetDialog,
        ):
            # These dialog classes need DB access; pass the ShotGrid conn.
            self._dialog = dialog_type(self._window, entity_list, self._conn)
        else:
            self._dialog = self._dialog_T(self._window, entity_list)

        if not self._dialog.exec_():
            return False

        selected_item = self._dialog.get_selected_item()
        if selected_item is None:
            MessageDialog(
                self._window,
                "Error: Nothing selected. Nothing exported",
                "Error",
            ).exec_()
            return False
        self._selected_item = selected_item

        if self._use_sg_entity:
            try:
                self._entity = self._get_entity_from_name(self._selected_item)
            except AssertionError:
                entity_label = SGEntity.__name__
                MessageDialog(
                    self._window,
                    "Error: The selected item did not correspond to a valid "
                    f"{entity_label} in ShotGrid. Please "
                    "report this error. Nothing exported",
                    "Error",
                ).exec_()
                return False
            log.debug(self._entity)

        return True

    def _do_publish_export(self) -> None:
        """Run the timed export, wrapped in a telemetry event.

        Subclasses with `_PUBLISH_KIND = None` publish without telemetry.
        """
        kind = self._publish_kind()
        if kind is None:
            self._mayausd_export_and_finalize()
            return

        with telemetry.record(
            telemetry.EVENT_PUBLISH_USD,
            payload={
                "kind": kind,
                "publish_path": str(self._publish_path),
            },
            **self._publish_scope_kwargs(),
        ):
            self._mayausd_export_and_finalize()

    def _mayausd_export_and_finalize(self) -> None:
        """The export → Windows fix-up → postpublish chain.

        Errors at each stage raise typed exceptions whose `error_code`
        attribute drives the telemetry event written by `record()`.
        """
        self._publish_path.parent.mkdir(parents=True, exist_ok=True)
        temp_publish_path = str(Path(os.getenv("TEMP", "")) / self._publish_path.name)

        kwargs = {
            "file": str(temp_publish_path if self._IS_WINDOWS else self._publish_path),
            "selection": True,
            "stripNamespaces": True,
            # "writeDefaults": True,
            **self._get_mayausd_kwargs(),
        }

        try:
            mc.mayaUSDExport(**kwargs)  # type: ignore
        except Exception as exc:
            log.exception("Maya USD export failed")
            MessageDialog(
                self._window,
                "WARNING: Publish failed! Please check the console for more information",
                "Export Failed",
            ).exec_()
            raise USDExportError(str(exc) or exc.__class__.__name__) from exc

        # On Windows, work around https://github.com/PixarAnimationStudios/OpenUSD/issues/849
        if self._IS_WINDOWS:
            try:
                shutil.move(temp_publish_path, self._publish_path)
            except Exception as exc:
                raise PublishCopyError(
                    f"Could not move publish from {temp_publish_path} to "
                    f"{self._publish_path}: {exc}"
                ) from exc

        try:
            self._postpublish()
        except Exception as exc:
            raise PublishCopyError(f"Postpublish step failed: {exc}") from exc

    def _postpublish(self) -> None:
        pass
