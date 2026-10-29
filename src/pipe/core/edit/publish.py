"""Publishing a cut: ShotGrid takes its ranges and its movie, and the team hears
what moved."""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

from env import discord_publish_webhook, discord_role_ids

from pipe.core.announce.discord import post_message
from pipe.core.edit.cut import Cut, record_published
from pipe.core.edit.diff import RangeChange
from pipe.core.edit.updates import rank
from pipe.core.shotgrid import Playlist, Shot, ShotGrid, ShotGridError
from pipe.core.util.text import and_list
from pipe.core.util.users import resolve_artist_display_name

log = logging.getLogger(__name__)

# A shot's range sets the scene range, playblasts and renders of each of these.
_TOLD = ["Animation", "CFX", "FX", "Lighting"]
_DISCORD_LIMIT = 2000
# Every cut's review movie, so the team watches cuts in one place.
_PLAYLIST = "Cuts"


def publish(
    sg: ShotGrid,
    path: Path,
    cut: Cut,
    changes: list[RangeChange],
    out_of_picture: list[Shot],
    reinstated: list[Shot],
    movie: Path | None,
) -> str:
    """Write the ranges and statuses, upload `movie` as the cut's review
    Version, announce what ShotGrid took, and record the cut as published only
    if it took every range and status. Returns the report."""
    written: list[RangeChange] = []
    removed: list[Shot] = []
    failed: list[str] = []
    for change in changes:
        try:
            sg.set_shot_cut_range(
                change.shot, cut_in=change.frames.start, cut_out=change.frames.stop - 1
            )
            written.append(change)
        except ShotGridError as exc:
            failed.append(f"{change.shot.code}: {exc}")
    for shot in out_of_picture:
        try:
            sg.set_shot_out_of_picture(shot)
            removed.append(shot)
        except ShotGridError as exc:
            failed.append(f"{shot.code}: {exc}")

    report = (
        f"ShotGrid took {len(written)} range{'' if len(written) == 1 else 's'}"
        f" and {len(removed)} Out of Picture."
    )
    if failed:
        report += (
            f"\n\nShotGrid refused these, so {path.stem} isn't recorded as "
            "published. Publish again to retry:\n" + "\n".join(failed)
        )
    else:
        record_published(path)
    uploaded = False
    if movie:
        uploaded, uploading = _upload(sg, path.stem, cut, movie)
        report += f"\n\n{uploading}"
    text = announcement(path.stem, cut, written, removed, reinstated, uploaded)
    return f"{report}\n\n{_announce(text)}"


def _upload(sg: ShotGrid, name: str, cut: Cut, movie: Path) -> tuple[bool, str]:
    """Whether the movie reached the playlist, and the report."""
    try:
        # The cut spans every sequence, so the Version hangs off the project
        # and is found through the playlist.
        version = sg.create_version(
            code=name, description=cut_status(cut), path_to_frames=movie
        )
    except ShotGridError as exc:
        return False, f"ShotGrid refused the review Version ({exc})."
    try:
        sg.upload_movie(version, movie)
    except ShotGridError as exc:
        return False, (
            f"The movie didn't upload ({exc}), leaving an empty Version {name} "
            "in ShotGrid."
        )
    try:
        sg.link_to_playlist(version, playlist_id=_playlist(sg).id)
    except ShotGridError as exc:
        return False, (
            f"Uploaded {name}, but couldn't add it to the {_PLAYLIST} playlist "
            f"({exc}), so add it by hand."
        )
    return True, f"Uploaded {name} to the {_PLAYLIST} playlist."


def _playlist(sg: ShotGrid) -> Playlist:
    found = sg.find_playlists(code_contains=_PLAYLIST)
    return next((p for p in found if p.code == _PLAYLIST), None) or sg.create_playlist(
        _PLAYLIST
    )


def announcement(
    name: str,
    cut: Cut,
    changes: list[RangeChange],
    out_of_picture: list[Shot],
    reinstated: list[Shot],
    uploaded: bool,
) -> str:
    lines = [f"**{name}** published by {resolve_artist_display_name()}."]
    if out_of_picture:
        lines.append(f"Out of Picture: {and_list(_codes(out_of_picture))}.")
    if reinstated:
        lines.append(
            "Back in the cut, still Out of Picture in ShotGrid: "
            f"{and_list(_codes(reinstated))}."
        )
    lines.append(cut_status(cut))
    if uploaded:
        lines.append(f"Watch it in the {_PLAYLIST} playlist on ShotGrid.")
    ranges = [f"{c.shot.code} {c.frames.start}-{c.frames.stop - 1}" for c in changes]
    # The first publish rewrites most of the show's ranges, more than one
    # Discord message holds.
    for shown in range(len(ranges), 0, -1):
        more = len(ranges) - shown
        listed = ", ".join(ranges[:shown] + ([f"{more} more"] if more else []))
        text = "\n".join(
            [
                lines[0],
                "New shot ranges, so check your scene range before you "
                f"playblast or render: {listed}.",
                *lines[1:],
            ]
        )
        if len(text) <= _DISCORD_LIMIT:
            return text
    return "\n".join(lines)


def cut_status(cut: Cut) -> str:
    """'137 shots: 40 previs, 50 anim, 12 lighting, 24 on old movies', counting
    each shot at its highest-ranked delivery."""
    best: dict[str, str] = {}
    for clip in cut.clips:
        code, department = clip.delivery.shot_code, clip.delivery.department
        if code not in best or rank(department) > rank(best[code]):
            best[code] = department
    old = {c.shot_code for c in cut.legacy} - best.keys()
    counts = Counter(best.values())
    parts = [f"{counts[d]} {d}" for d in sorted(counts, key=rank)]
    if old:
        parts.append(f"{len(old)} on old movies")
    return f"Cut: {len(best) + len(old)} shots, {', '.join(parts)}."


def _announce(text: str) -> str:
    if discord_publish_webhook is None:
        return "Discord isn't configured in env.py, so nothing was posted."
    tagged = [step for step in _TOLD if step in discord_role_ids]
    try:
        post_message(
            discord_publish_webhook, text, [discord_role_ids[s] for s in tagged]
        )
    except Exception as exc:
        log.exception("Discord announcement failed")
        return f"Discord: couldn't post ({exc}). Tell {and_list(_TOLD)} yourself."
    return f"Discord: told {and_list(tagged)}."


def _codes(shots: list[Shot]) -> list[str]:
    return [str(s.code) for s in shots]
