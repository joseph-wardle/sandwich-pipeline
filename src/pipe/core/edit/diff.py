"""What publishing a cut would change in ShotGrid. Reads only.

python -m pipe.core.edit.diff                      the latest saved cut
python -m pipe.core.edit.diff cut.otio --since published.otio
"""

from __future__ import annotations

import argparse
from pathlib import Path

import attrs

from pipe.core.edit.cut import Cut, read_cut, saved_cuts, shot_spans
from pipe.core.shotgrid import Shot, ShotGrid

# A clip can show handle frames past ShotGrid's range. Frames further out mean
# its movie isn't numbered in shot frames, like the 2026-10-08 previs backfill
# numbered in scene time.
_MAX_OUTSIDE = 24


@attrs.frozen
class RangeChange:
    shot: Shot
    frames: range


@attrs.frozen
class Diff:
    changes: list[RangeChange]
    """Shots whose ShotGrid range isn't what the cut shows, Out of Picture
    ones included."""
    unread: dict[str, str]
    """Shots whose range can't be read from the cut, and why."""
    reinstated: list[Shot]
    """Out of Picture shots that are in the cut."""
    dropped: list[Shot]
    """Shots in the published cut that this cut leaves out."""
    unknown: list[str]
    """Shots in the cut that ShotGrid doesn't have."""


def compare(
    cut: Cut, shots: list[Shot], out_of_picture: list[Shot], published: Cut | None
) -> Diff:
    known = {s.code: s for s in [*shots, *out_of_picture]}
    unread = {
        c.shot_code: f"{c.path.name} isn't a delivery in shots/{c.shot_code}"
        for c in cut.legacy
    }
    for clip in cut.clips:
        shot = known.get(clip.delivery.shot_code)
        if shot is None or shot.cut_in is None or shot.cut_out is None:
            continue
        first, last = clip.frames.start, clip.frames.stop - 1
        if first < shot.cut_in - _MAX_OUTSIDE or last > shot.cut_out + _MAX_OUTSIDE:
            unread.setdefault(
                clip.delivery.shot_code,
                f"{clip.delivery.path.name} shows {first}-{last}, ShotGrid has "
                f"{shot.cut_in}-{shot.cut_out}. Is its timecode in shot frames?",
            )
    spans = shot_spans([c for c in cut.clips if c.delivery.shot_code not in unread])
    changes = [
        RangeChange(known[code], span.frames)
        for code, span in spans.items()
        if code in known
        and (known[code].cut_in, known[code].cut_out)
        != (span.frames.start, span.frames.stop - 1)
    ]
    in_cut = _codes(cut)
    active = {s.code for s in shots}
    return Diff(
        changes,
        {code: reason for code, reason in unread.items() if code in known},
        [s for s in out_of_picture if s.code in in_cut],
        [
            known[code]
            for code in (_codes(published) if published else [])
            if code in active and code not in in_cut
        ],
        [code for code in in_cut if code not in known],
    )


def _codes(cut: Cut) -> list[str]:
    """Shots in the cut, legacy clips included, in timeline order of deliveries
    then legacy clips."""
    codes = [c.delivery.shot_code for c in cut.clips] + [
        c.shot_code for c in cut.legacy
    ]
    return list(dict.fromkeys(codes))


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare a cut with ShotGrid.")
    parser.add_argument("cut", nargs="?", type=Path, help="latest saved cut if none")
    parser.add_argument("--since", type=Path, help="the last published cut")
    args = parser.parse_args()

    from env_sg import DB_Config

    path = args.cut or (saved_cuts() or [None])[-1]
    if path is None:
        raise SystemExit("No saved cuts. Name an OTIO file.")
    sg = ShotGrid.connect(DB_Config)
    diff = compare(
        read_cut(path),
        sg.find_shots(),
        sg.find_shots(out_of_picture=True),
        read_cut(args.since) if args.since else None,
    )
    print(f"{path.name} against ShotGrid")
    _section(
        "Range changes",
        [
            f"{c.shot.code:<12} {_range(c.shot.cut_in, c.shot.cut_out):<20} -> "
            f"{_range(c.frames.start, c.frames.stop - 1)}"
            for c in diff.changes
        ],
    )
    _section("Can't read the range", [f"{k:<12} {v}" for k, v in diff.unread.items()])
    _section("Out of Picture, back in the cut", [str(s.code) for s in diff.reinstated])
    if args.since:
        _section(
            f"Gone since {args.since.stem}, still active",
            [str(s.code) for s in diff.dropped],
        )
    else:
        print("\nNo --since cut, so dropped shots weren't checked.")
    _section("Not in ShotGrid", diff.unknown)


def _section(title: str, lines: list[str]) -> None:
    print(f"\n{title} ({len(lines)})")
    for line in lines:
        print(f"  {line}")


def _range(first: int | None, last: int | None) -> str:
    if first is None or last is None:
        return "no range"
    return f"{first}-{last} ({last - first + 1}f)"


if __name__ == "__main__":
    main()
