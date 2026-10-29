from __future__ import annotations

from typing import Any


def root_bin(media_pool: Any, name: str) -> Any:
    """The bin `name` at the media pool's root, made if missing."""
    root = media_pool.GetRootFolder()
    return next(
        (f for f in root.GetSubFolderList() if f.GetName() == name), None
    ) or media_pool.AddSubFolder(root, name)


def message(fusion: Any, bmd: Any, title: str, text: str) -> None:
    ui = fusion.UIManager
    dispatcher = bmd.UIDispatcher(ui)
    window = dispatcher.AddWindow(
        {
            "ID": "SKDMessage",
            "WindowTitle": title,
            "Geometry": [300, 300, 480, 160],
        },
        ui.VGroup(
            [
                ui.Label({"Text": text, "WordWrap": True}),
                ui.HGroup(
                    {"Weight": 0}, [ui.HGap(), ui.Button({"ID": "OK", "Text": "OK"})]
                ),
            ]
        ),
    )

    def close(_event: Any) -> None:
        dispatcher.ExitLoop()

    window.On.OK.Clicked = close
    window.On.SKDMessage.Close = close
    window.Show()
    dispatcher.RunLoop()
    window.Hide()
