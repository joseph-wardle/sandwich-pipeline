from pipe.maya.command import maya_command


@maya_command(
    name="reload_pipe",
    label="Reload Pipe",
    hotkey="ctrl+alt+r",
    icon="cycle.png",
    category="development",
)
def reload_pipe() -> None:
    """
    Unloads the pipeline python modules for testing modifications during development.
    """
    from pipe.util import reload_pipe as _reload_pipe

    _reload_pipe()

    # wrap this in a try block because it will fail in headless mode
    try:
        import mayaUsd.lib as mayaUsdLib  # type: ignore[import-not-found]

        from pipe.maya.publish import ExportChaser

        mayaUsdLib.ExportChaser.Unregister(ExportChaser, ExportChaser.ID)
        mayaUsdLib.ExportChaser.Register(ExportChaser, ExportChaser.ID)
    except Exception:
        pass
