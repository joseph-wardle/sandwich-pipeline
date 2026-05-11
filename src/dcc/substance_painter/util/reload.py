import substance_painter_plugins as spp
from core.util import reload_pipeline


def reload_pipe() -> None:
    sp_plugins = [
        spp.plugins["export"],
        spp.plugins["shelf"],
    ]
    reload_pipeline(sp_plugins)

    for plugin in sp_plugins:
        plugin.close_plugin()
        plugin.start_plugin()
