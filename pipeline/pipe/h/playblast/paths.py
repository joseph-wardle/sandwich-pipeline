from __future__ import annotations

from datetime import datetime
from pathlib import Path

from shared.util import get_edit_path

from pipe.playblast_naming import playblast_date_folder


def build_edit_output_directory(
    department: str, timestamp: datetime | None = None
) -> Path:
    return get_edit_path() / department / playblast_date_folder(timestamp)
