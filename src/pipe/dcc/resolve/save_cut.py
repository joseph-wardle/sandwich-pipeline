"""SKD › Save Cut: a frozen copy of the open timeline in the Cuts bin, and the
same cut as `edit/cuts/cut_###.otio` for the tools that read it. Ticking
Publish to ShotGrid then writes the cut's shot ranges to ShotGrid and uploads
the cut as `cut_###.mp4` for review."""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Any

from pipe.core.edit.cut import (
    Cut,
    next_cut_path,
    published_cut,
    read_cut,
    shot_spans,
)
from pipe.core.edit.diff import Diff, RangeChange, compare
from pipe.core.edit.publish import publish
from pipe.core.shotgrid import Shot, ShotGrid, ShotGridError
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
    answer = _ask(fusion, bmd, timeline.GetName(), prefix)
    if answer is None:
        return
    label, publishing = answer
    name = f"{prefix} · {label}" if label else prefix
    saved, report = _save(resolve, project, timeline, path, name)
    if saved and publishing:
        snapshot, cut = saved
        report += "\n\n" + _publish(resolve, fusion, bmd, snapshot, path, cut)
    message(fusion, bmd, TITLE, report)


def _save(
    resolve: Any, project: Any, timeline: Any, path: Path, name: str
) -> tuple[tuple[Any, Cut] | None, str]:
    """The snapshot timeline and its cut, or None if nothing was saved, and the
    report."""
    media_pool = project.GetMediaPool()
    snapshot = timeline.DuplicateTimeline(name)
    if not snapshot:
        return None, f"Resolve couldn't duplicate {timeline.GetName()}."
    path.parent.mkdir(exist_ok=True)
    if not snapshot.Export(str(path), resolve.EXPORT_OTIO, resolve.EXPORT_NONE):
        media_pool.DeleteTimelines([snapshot])
        return None, f"Resolve couldn't write {path}, so no cut was saved."
    media_pool.MoveClips([snapshot.GetMediaPoolItem()], root_bin(media_pool, CUTS_BIN))
    project.SetCurrentTimeline(timeline)

    cut = read_cut(path)
    report = f"Saved {name}, {len(shot_spans(cut.clips))} shots."
    if cut.legacy:
        names = dict.fromkeys(f"{c.shot_code} ({c.path.name})" for c in cut.legacy)
        report += "\n\nNot counted, their timecode isn't a shot frame:\n"
        report += "\n".join(names)
    return (snapshot, cut), report


def _ask(
    fusion: Any, bmd: Any, timeline_name: str, prefix: str
) -> tuple[str, bool] | None:
    """The label typed and whether to publish, or None if the editor
    cancelled."""
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
                        ui.CheckBox({"ID": "Publish", "Text": "Publish to ShotGrid"}),
                        ui.HGap(),
                        ui.Button({"ID": "Save", "Text": "Save"}),
                        ui.Button({"ID": "Cancel", "Text": "Cancel"}),
                    ],
                ),
            ]
        ),
    )
    items = window.GetItems()
    answer: tuple[str, bool] | None = None

    def save(_event: Any) -> None:
        nonlocal answer
        answer = items["Label"].Text.strip(), bool(items["Publish"].Checked)
        dispatcher.ExitLoop()

    def close(_event: Any) -> None:
        dispatcher.ExitLoop()

    window.On.Save.Clicked = save
    window.On.Cancel.Clicked = close
    window.On.SKDSaveCut.Close = close
    window.Show()
    dispatcher.RunLoop()
    window.Hide()
    return answer


def _publish(
    resolve: Any, fusion: Any, bmd: Any, snapshot: Any, path: Path, cut: Cut
) -> str:
    from env_sg import DB_Config

    since = published_cut()
    try:
        sg = ShotGrid.connect(DB_Config)
        diff = compare(
            cut,
            sg.find_shots(),
            sg.find_shots(out_of_picture=True),
            read_cut(since) if since else None,
        )
    except ShotGridError as exc:
        return f"Couldn't read ShotGrid ({exc}), so nothing was published."
    chosen = _confirm(fusion, bmd, path.stem, diff, since)
    if chosen is None:
        return "Not published to ShotGrid."
    changes, out_of_picture = chosen
    movie = path.with_suffix(".mp4")
    failed = _render(resolve, snapshot, movie)
    report = publish(
        sg,
        path,
        cut,
        changes,
        out_of_picture,
        diff.reinstated,
        None if failed else movie,
    )
    return (
        f"{failed} No review movie went to ShotGrid.\n\n{report}" if failed else report
    )


def _render(resolve: Any, timeline: Any, movie: Path) -> str | None:
    """Render `timeline` to `movie` at review size, or say why it couldn't.
    This replaces the Deliver page's settings, which Resolve can't read back."""
    project = resolve.GetProjectManager().GetCurrentProject()
    page, current = resolve.GetCurrentPage(), project.GetCurrentTimeline()
    # Render jobs take the current timeline.
    project.SetCurrentTimeline(timeline)
    try:
        # Resolve's only H.264 encoder is the NVIDIA one.
        if not project.SetCurrentRenderFormatAndCodec("mp4", "H264_NVIDIA"):
            return "Resolve has no NVIDIA H.264 encoder here."
        project.SetRenderSettings(
            {
                "SelectAllFrames": True,
                "TargetDir": str(movie.parent),
                "CustomName": movie.stem,
                "FormatWidth": 1920,
                "FormatHeight": 1080,
                "ExportVideo": True,
                "ExportAudio": True,
                "VideoQuality": 8000,
            }
        )
        job = project.AddRenderJob()
        if not job:
            return "Resolve couldn't queue the render."
        project.StartRendering([job], isInteractiveMode=False)
        while project.IsRenderingInProgress():
            time.sleep(1)
        status = project.GetRenderJobStatus(job)
        project.DeleteRenderJob(job)
    finally:
        project.SetCurrentTimeline(current)
        resolve.OpenPage(page)
    if status.get("JobStatus") != "Complete":
        return f"The render stopped: {status.get('Error') or status.get('JobStatus')}."
    return None


def _confirm(
    fusion: Any, bmd: Any, name: str, diff: Diff, since: Path | None
) -> tuple[list[RangeChange], list[Shot]] | None:
    """The ticked range changes and dropped shots, or None if the editor
    cancelled."""
    warnings = []
    if since is None:
        warnings.append("No cut is published yet, so dropped shots can't be listed.")
    if diff.dropped:
        warnings.append(
            "Ticking a dropped shot marks it Out of Picture. That needs the "
            "producer's and the director's sign-off, so tick one only once "
            "they have."
        )
    ui = fusion.UIManager
    dispatcher = bmd.UIDispatcher(ui)
    window = dispatcher.AddWindow(
        {
            "ID": "SKDPublish",
            "WindowTitle": f"Publish {name} to ShotGrid",
            "Geometry": [200, 200, 1000, 600],
        },
        ui.VGroup(
            [
                *[
                    ui.Label({"Text": w, "WordWrap": True, "Weight": 0})
                    for w in warnings
                ],
                ui.Tree({"ID": "Rows", "SortingEnabled": False}),
                ui.HGroup(
                    {"Weight": 0},
                    [
                        ui.HGap(),
                        ui.Button({"ID": "Publish", "Text": "Publish"}),
                        ui.Button({"ID": "Cancel", "Text": "Cancel"}),
                    ],
                ),
            ]
        ),
    )
    tree = window.GetItems()["Rows"]
    header = tree.NewItem()
    for column, text in enumerate(("Shot", "ShotGrid", "Cut", "")):
        header.Text[column] = text
    tree.SetHeaderItem(header)
    tree.ColumnCount = 4

    rows: list[tuple[Any, RangeChange | Shot]] = []

    def add(code: str, sg: str, cut: str, note: str, ticked: bool | None) -> Any:
        item = tree.NewItem()
        for column, text in enumerate((code, sg, cut, note)):
            item.Text[column] = text
        if ticked is None:
            item.Disabled = True
        else:
            item.CheckState[0] = "Checked" if ticked else "Unchecked"
        tree.AddTopLevelItem(item)
        return item

    for change in diff.changes:
        shot = change.shot
        first, last = change.frames.start, change.frames.stop - 1
        note = ""
        if shot in diff.reinstated:
            note = "Out of Picture, back in the cut"
        elif shot.cut_in is not None and shot.cut_out is not None:
            if first < shot.cut_in or last > shot.cut_out:
                note = "longer than ShotGrid's range"
        item = add(str(shot.code), _range(shot), f"{first}-{last}", note, True)
        rows.append((item, change))
    changed = {c.shot for c in diff.changes}
    for shot in diff.reinstated:
        if shot not in changed:
            add(
                str(shot.code),
                _range(shot),
                "",
                "Out of Picture, back in the cut",
                None,
            )
    for shot in diff.dropped:
        note = f"gone since {since.stem if since else ''}: tick for Out of Picture"
        rows.append((add(str(shot.code), _range(shot), "gone", note, False), shot))
    for code, reason in diff.unread.items():
        add(code, "", "", reason, None)
    for code in diff.unknown:
        add(code, "", "", "not in ShotGrid", None)
    tree.ColumnWidth[0] = 110
    tree.ColumnWidth[1] = 110
    tree.ColumnWidth[2] = 110

    chosen: tuple[list[RangeChange], list[Shot]] | None = None

    def accept(_event: Any) -> None:
        nonlocal chosen
        ticked = [row for item, row in rows if item.CheckState[0] == "Checked"]
        chosen = (
            [row for row in ticked if isinstance(row, RangeChange)],
            [row for row in ticked if isinstance(row, Shot)],
        )
        dispatcher.ExitLoop()

    def close(_event: Any) -> None:
        dispatcher.ExitLoop()

    window.On.Publish.Clicked = accept
    window.On.Cancel.Clicked = close
    window.On.SKDPublish.Close = close
    window.Show()
    dispatcher.RunLoop()
    window.Hide()
    return chosen


def _range(shot: Shot) -> str:
    if shot.cut_in is None or shot.cut_out is None:
        return "no range"
    return f"{shot.cut_in}-{shot.cut_out}"
