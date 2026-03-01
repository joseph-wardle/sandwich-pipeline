import logging
from contextlib import contextmanager
from pathlib import Path

from shared.util import get_production_path

log = logging.getLogger(__name__)

CUSTOM_PICKER_TITLE = "SKD Picker"


@contextmanager
def custom_picker_title(custom_title: str):
    import dwpicker.main

    original_title = dwpicker.main.WINDOW_TITLE
    try:
        dwpicker.main.WINDOW_TITLE = custom_title
        yield
    finally:
        dwpicker.main.WINDOW_TITLE = original_title


def run():
    picker_folder_path = get_production_path() / "pickers"
    if not picker_folder_path.exists():
        log.warning(
            f"No picker folder found at {picker_folder_path}. Skipping picker file loading."
        )
        open_picker()
        return

    picker_filepaths = [
        picker_file
        for picker_file in picker_folder_path.iterdir()
        if picker_file.suffix == ".json"
    ]
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
