from typing import Any, cast

import studiolibrary  # type: ignore[import-not-found]
from core.util.paths import get_anim_path


def run():
    studio_module = cast(Any, studiolibrary)
    libraries = [
        {
            "name": "Bobo Poses",
            "path": str(get_anim_path() / "studiolibrary/bobo-poses"),
            "default": True,
            "theme": {
                "accentColor": "rgb(97, 30, 10)",
            },
        },
    ]
    studio_module.setLibraries(libraries)
    studio_module.main()
