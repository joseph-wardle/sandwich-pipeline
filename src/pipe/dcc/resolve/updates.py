"""SKD › Updates: newer deliveries for the clips on the open timeline, added as
takes so the editor can flip back."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import attrs

from pipe.core.edit import (
    Offer,
    delivery_at,
    legacy_shot_code,
    offers,
    rank,
    shot_deliveries,
)
from pipe.dcc.resolve.util import message, root_bin

IMPORT_BIN = "Deliveries"
TITLE = "Updates"


@attrs.define
class _Row:
    shot_code: str
    in_cut: str
    newer: str
    note: str
    offer: Offer | None = None
    # (timeline item, frames of the offered movie it gets) per clip
    takes: list[tuple[Any, range]] = attrs.Factory(list)


def main(resolve: Any, bmd: Any) -> None:
    fusion = resolve.Fusion()
    project = resolve.GetProjectManager().GetCurrentProject()
    timeline = project and project.GetCurrentTimeline()
    if not timeline:
        message(fusion, bmd, TITLE, "Open a timeline first.")
        return
    rows = _rows(timeline)
    if not rows:
        message(
            fusion,
            bmd,
            TITLE,
            f"Every shot on {timeline.GetName()} uses its newest delivery.",
        )
        return
    chosen = _choose(fusion, bmd, timeline.GetName(), rows)
    if chosen == []:
        message(fusion, bmd, TITLE, "Nothing ticked, no takes added.")
    elif chosen:
        message(fusion, bmd, TITLE, _add_takes(project.GetMediaPool(), chosen))


def _rows(timeline: Any) -> list[_Row]:
    rows: dict[tuple[str, str], _Row] = {}
    for item in _clips(timeline):
        path = Path(item.GetMediaPoolItem().GetClipProperty("File Path"))
        current = delivery_at(path)
        if current is None:
            _add_legacy_row(rows, path)
            continue
        left = int(item.GetLeftOffset())
        used = range(left, left + int(item.GetDuration()))
        held = _take_paths(item)
        for offer in offers(current, used):
            if offer.delivery.path.name in held:
                continue
            key = (path.name, offer.delivery.path.name)
            row = rows.get(key)
            if row is None:
                row = rows[key] = _Row(
                    current.shot_code,
                    path.name,
                    offer.delivery.path.name,
                    "",
                    offer,
                )
            if offer.take is None:
                row.offer = offer
                row.note = offer.problem
            else:
                row.takes.append((item, offer.take))
    return list(rows.values())


def _add_legacy_row(rows: dict[tuple[str, str], _Row], path: Path) -> None:
    shot_code = legacy_shot_code(path)
    if shot_code is None or (path.name, "") in rows:
        return
    deliveries, _ = shot_deliveries(shot_code)
    if deliveries:
        rows[path.name, ""] = _Row(
            shot_code,
            path.name,
            deliveries[-1].path.name,
            "replace by hand once: its timecode isn't a shot frame",
        )


def _clips(timeline: Any) -> list[Any]:
    """Enabled clips with a movie behind them, on enabled video tracks, in
    timeline order."""
    clips = [
        item
        for track in range(1, timeline.GetTrackCount("video") + 1)
        if timeline.GetIsTrackEnabled("video", track)
        for item in timeline.GetItemListInTrack("video", track) or []
        if item.GetClipEnabled()
        and item.GetMediaPoolItem()
        and item.GetMediaPoolItem().GetClipProperty("File Path")
    ]
    return sorted(clips, key=lambda item: item.GetStart())


def _take_paths(item: Any) -> set[str]:
    takes = (item.GetTakeByIndex(i) for i in range(1, item.GetTakesCount() + 1))
    return {
        Path(take["mediaPoolItem"].GetClipProperty("File Path")).name
        for take in takes
        if take
    }


def _choose(
    fusion: Any, bmd: Any, timeline_name: str, rows: list[_Row]
) -> list[_Row] | None:
    """The ticked rows, or None if the editor cancelled."""
    ui = fusion.UIManager
    dispatcher = bmd.UIDispatcher(ui)
    window = dispatcher.AddWindow(
        {
            "ID": "SKDUpdates",
            "WindowTitle": f"Updates · {timeline_name}",
            "Geometry": [200, 200, 1000, 600],
        },
        ui.VGroup(
            [
                ui.Tree({"ID": "Rows", "SortingEnabled": False}),
                ui.HGroup(
                    {"Weight": 0},
                    [
                        ui.HGap(),
                        ui.Button({"ID": "Add", "Text": "Add Takes"}),
                        ui.Button({"ID": "Cancel", "Text": "Cancel"}),
                    ],
                ),
            ]
        ),
    )
    tree = window.GetItems()["Rows"]
    header = tree.NewItem()
    for column, text in enumerate(("Shot", "In cut", "Newer", "")):
        header.Text[column] = text
    tree.SetHeaderItem(header)
    tree.ColumnCount = 4
    items = []
    for row in rows:
        item = tree.NewItem()
        note = row.note
        if len(row.takes) > 1:
            note += f" · {len(row.takes)} clips"
        for column, text in enumerate((row.shot_code, row.in_cut, row.newer, note)):
            item.Text[column] = text
        if row.takes and row.offer and row.offer.take is not None:
            ticked = row.offer.preferred
            item.CheckState[0] = "Checked" if ticked else "Unchecked"
        else:
            item.Disabled = True
        tree.AddTopLevelItem(item)
        items.append(item)
    for column in range(3):
        tree.ColumnWidth[column] = 220 if column else 110

    chosen: list[_Row] | None = None

    def add(_event: Any) -> None:
        nonlocal chosen
        chosen = list(
            row
            for row, item in zip(rows, items, strict=True)
            if not item.Disabled and item.CheckState[0] == "Checked"
        )
        dispatcher.ExitLoop()

    def close(_event: Any) -> None:
        dispatcher.ExitLoop()

    window.On.Add.Clicked = add
    window.On.Cancel.Clicked = close
    window.On.SKDUpdates.Close = close
    window.Show()
    dispatcher.RunLoop()
    window.Hide()
    return chosen


def _add_takes(media_pool: Any, rows: list[_Row]) -> str:
    # Highest rank last, so its take is the one left selected.
    offered = sorted(
        ((row, row.offer) for row in rows if row.offer),
        key=lambda pair: rank(pair[1].delivery.department),
    )
    pool_items = _pool_items(media_pool, [offer.delivery.path for _, offer in offered])
    added = 0
    failed: list[str] = []
    for row, _ in offered:
        pool_item = pool_items.get(row.newer)
        for item, take in row.takes:
            if pool_item and item.AddTake(pool_item, take.start, take.stop - 1):
                item.SelectTakeByIndex(item.GetTakesCount())
                added += 1
            else:
                failed.append(f"{row.shot_code} at {item.GetStart()}: {row.newer}")
    report = f"Added {added} take{'' if added == 1 else 's'}."
    if failed:
        report += "\n\nResolve refused these:\n" + "\n".join(failed)
    return report


def _pool_items(media_pool: Any, paths: list[Path]) -> dict[str, Any]:
    """Media pool clips for `paths` by file name, importing any the pool lacks
    into the Deliveries bin."""
    found: dict[str, Any] = {}
    wanted = {path.name for path in paths}
    folders = [media_pool.GetRootFolder()]
    while folders:
        folder = folders.pop()
        folders.extend(folder.GetSubFolderList() or [])
        for clip in folder.GetClipList() or []:
            name = Path(clip.GetClipProperty("File Path")).name
            if name in wanted:
                found[name] = clip
    missing = [str(path) for path in paths if path.name not in found]
    if missing:
        previous = media_pool.GetCurrentFolder()
        media_pool.SetCurrentFolder(root_bin(media_pool, IMPORT_BIN))
        for clip in media_pool.ImportMedia(missing) or []:
            found[Path(clip.GetClipProperty("File Path")).name] = clip
        media_pool.SetCurrentFolder(previous)
    return found
