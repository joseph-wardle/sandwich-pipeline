import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from pipe.core.util.paths import get_anim_path

from pipe.dcc.maya.command import maya_command

log = logging.getLogger(__name__)

CUSTOM_PICKER_TITLE = "SKD Picker"


@contextmanager
def custom_picker_title(custom_title: str):
    import dwpicker.main

    main_module = cast(Any, dwpicker.main)
    original_title = main_module.WINDOW_TITLE
    try:
        main_module.WINDOW_TITLE = custom_title
        yield
    finally:
        main_module.WINDOW_TITLE = original_title


@maya_command(
    name="picker",
    label="Picker",
    icon="picker.svg",
    hotkey="ctrl+alt+p",
    category="animation",
)
def run():
    """
    Load the pipeline picker UI.
    """
    picker_folder_path = get_anim_path() / "pickers"
    if not picker_folder_path.exists():
        log.warning(
            f"No picker folder found at {picker_folder_path}. Skipping picker file loading."
        )
        open_picker()
        return

    # Pickers live in per-character subfolders (pickers/<char>/<char>_Body.json),
    # so recurse. Skip Versions/ folders, which hold numbered backups.
    picker_filepaths = [
        picker_file
        for picker_file in picker_folder_path.rglob("*.json")
        if "Versions" not in picker_file.parts
    ]
    if not picker_filepaths:
        log.warning(
            f"No picker files found under {picker_folder_path}. Opening an empty picker."
        )
        open_picker()
        return

    log.info(
        f"Loading Picker with files: {[str(picker) for picker in picker_filepaths]}"
    )

    open_picker(picker_files=picker_filepaths)


def open_picker(picker_files: list[Path] | None = None):
    import dwpicker

    with custom_picker_title(CUSTOM_PICKER_TITLE):
        if picker_files:
            picker_filepath_strings = [str(picker_file) for picker_file in picker_files]
            dwpicker.show(pickers=picker_filepath_strings)
        else:
            dwpicker.show()
