"""SKD › Save Cut: a frozen copy of the open timeline in the Cuts bin, and the
same cut as `edit/cuts/cut_###.otio` for the tools that read it."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from pipe.core.edit.cut import next_cut_path, read_cut, shot_spans
from pipe.dcc.resolve.util import message, root_bin

CUTS_BIN = "Cuts"
TITLE = "Save Cut"


def main(resolve: Any, bmd: Any) -> None:
    fusion = resolve.Fusion()
    project = resolve.GetProjectManager().GetCurrentProject()
    timeline = project and project.GetCurrentTimeline()
    if not timeline:
        message(fusion, bmd, TITLE, "Open a timeline first.")
        return
    path = next_cut_path()
    prefix = f"{path.stem} · {date.today().isoformat()}"
    label = _ask_label(fusion, bmd, timeline.GetName(), prefix)
    if label is None:
        return
    name = f"{prefix} · {label}" if label else prefix
    message(fusion, bmd, TITLE, _save(resolve, project, timeline, path, name))


def _save(resolve: Any, project: Any, timeline: Any, path: Path, name: str) -> str:
    media_pool = project.GetMediaPool()
    snapshot = timeline.DuplicateTimeline(name)
    if not snapshot:
        return f"Resolve couldn't duplicate {timeline.GetName()}."
    path.parent.mkdir(exist_ok=True)
    if not snapshot.Export(str(path), resolve.EXPORT_OTIO, resolve.EXPORT_NONE):
        media_pool.DeleteTimelines([snapshot])
        return f"Resolve couldn't write {path}, so no cut was saved."
    media_pool.MoveClips([snapshot.GetMediaPoolItem()], root_bin(media_pool, CUTS_BIN))
    project.SetCurrentTimeline(timeline)

    cut = read_cut(path)
    report = f"Saved {name}, {len(shot_spans(cut.clips))} shots."
    if cut.legacy:
        names = dict.fromkeys(f"{c.shot_code} ({c.path.name})" for c in cut.legacy)
        report += "\n\nNot counted, their timecode isn't a shot frame:\n"
        report += "\n".join(names)
    return report


def _ask_label(fusion: Any, bmd: Any, timeline_name: str, prefix: str) -> str | None:
    """The label typed, or None if the editor cancelled."""
    ui = fusion.UIManager
    dispatcher = bmd.UIDispatcher(ui)
    window = dispatcher.AddWindow(
        {
            "ID": "SKDSaveCut",
            "WindowTitle": f"Save Cut · {timeline_name}",
            "Geometry": [300, 300, 480, 90],
        },
        ui.VGroup(
            [
                ui.HGroup(
                    {"Weight": 0},
                    [
                        ui.Label({"Text": f"{prefix} ·", "Weight": 0}),
                        ui.LineEdit({"ID": "Label", "PlaceholderText": "label"}),
                    ],
                ),
                ui.HGroup(
                    {"Weight": 0},
                    [
                        ui.HGap(),
                        ui.Button({"ID": "Save", "Text": "Save"}),
                        ui.Button({"ID": "Cancel", "Text": "Cancel"}),
                    ],
                ),
            ]
        ),
    )
    line = window.GetItems()["Label"]
    label: str | None = None

    def save(_event: Any) -> None:
        nonlocal label
        label = line.Text.strip()
        dispatcher.ExitLoop()

    def close(_event: Any) -> None:
        dispatcher.ExitLoop()

    window.On.Save.Clicked = save
    window.On.Cancel.Clicked = close
    window.On.SKDSaveCut.Close = close
    window.Show()
    dispatcher.RunLoop()
    window.Hide()
    return label
