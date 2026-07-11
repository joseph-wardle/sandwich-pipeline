from typing import Any, cast

import studiolibrary  # type: ignore[import-not-found]
from pipe.core.util.paths import get_anim_path

LIBRARY_NAME = "SKD Poses"


def run():
    studio_module = cast(Any, studiolibrary)
    library_path = get_anim_path() / "studio_library"
    libraries = [
        {
            "name": LIBRARY_NAME,
            "path": str(library_path),
            "default": True,
            "theme": {
                "accentColor": "rgb(97, 30, 10)",
            },
        },
    ]
    studio_module.setLibraries(libraries)

    # Pass the path explicitly so the window opens on our library even when the
    # user's saved LibraryWidget.json has a stale or empty root path — an
    # unresolved root path is what triggers the "choose a folder" welcome dialog.
    studio_module.main(name=LIBRARY_NAME, path=str(library_path))
