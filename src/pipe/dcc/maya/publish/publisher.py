from __future__ import annotations

import logging
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import maya.cmds as mc
from env_sg import DB_Config

from pipe.dcc.maya import runtime as maya_runtime
from pipe.core.ui import FilteredListDialog, MessageDialog
from pipe.dcc.maya.util.selection import maintain_selection
from pipe.dcc.maya.util.usd_export import export_selection
from pipe.core.shotgrid import SGEntity, ShotGrid

if TYPE_CHECKING:
    from Qt.QtWidgets import QWidget

log = logging.getLogger(__name__)


class USDExportError(Exception):
    """Raised when the Maya USD export fails."""


class PublishCopyError(Exception):
    """Raised when copying a published file into its final publish location fails."""


class Publisher:
    """Class for publishing USDs out of Maya"""

    _conn: ShotGrid
    _dialog: FilteredListDialog
    _dialog_T: type[FilteredListDialog]
    _entity: SGEntity
    _publish_path: Path
    _selected_item: str
    _use_sg_entity: bool
    _window: QWidget | None

    def __init__(
        self, dialog: type[FilteredListDialog] | None = None, use_sg_entity: bool = True
    ) -> None:
        self._conn = ShotGrid.connect(DB_Config)
        self._window = maya_runtime.get_main_qt_window()
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

            self._mayausd_export_and_finalize()

            MessageDialog(
                self._window, self._get_confirm_message(), "Export Complete"
            ).exec_()

    def _select_publish_target_or_cancel(self, entity_list: list[str]) -> bool:
        """Run the entity-selection dialog and populate `self._selected_item`/`self._entity`.

        Returns True if an entity was successfully selected. Returns False if
        the user cancelled or the selection was invalid — in which case an
        artist-facing dialog has already been shown.
        """
        from pipe.dcc.maya.publish.asset import (
            PublishAssetOptionsDialog,
            PublishAssetPickerDialog,
        )
        from pipe.dcc.maya.publish.previs_asset import PublishPrevisAssetDialog

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

    def _mayausd_export_and_finalize(self) -> None:
        """The export → Windows fix-up → postpublish chain."""
        try:
            export_selection(
                self._publish_path,
                **{"stripNamespaces": True, **self._get_mayausd_kwargs()},
            )
        except Exception as exc:
            log.exception("Maya USD export failed")
            MessageDialog(
                self._window,
                "WARNING: Publish failed! Please check the console for more information",
                "Export Failed",
            ).exec_()
            raise USDExportError(str(exc) or exc.__class__.__name__) from exc

        try:
            self._postpublish()
        except Exception as exc:
            raise PublishCopyError(f"Postpublish step failed: {exc}") from exc

    def _postpublish(self) -> None:
        pass
