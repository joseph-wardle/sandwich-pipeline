import importlib

import substance_painter_plugins as spp

from pipe.core.util import reload_pipeline


def reload_pipe() -> None:
    """Reload the pipeline modules and restart the SKD menu plugin."""
    # Looked up by name, not in `spp.plugins`, so a reload that failed on broken
    # code can be run again once the code is fixed.
    export = importlib.import_module("export")
    spp.close_plugin(export)
    reload_pipeline()
    spp.start_plugin(importlib.reload(export))
