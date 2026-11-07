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
    # UIManager windows don't fit their content, so size to the wrapped text,
    # and past the cap the box scrolls, as a list of ShotGrid refusals can.
    lines = sum(1 + len(line) // 60 for line in text.split("\n"))
    window = dispatcher.AddWindow(
        {
            "ID": "SKDMessage",
            "WindowTitle": title,
            "Geometry": [300, 300, 520, min(100 + 20 * lines, 600)],
        },
        ui.VGroup(
            [
                ui.TextEdit({"Text": text, "ReadOnly": True}),
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
