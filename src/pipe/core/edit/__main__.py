"""Census of editorial deliveries.

python -m pipe.core.edit              one line per shot folder
python -m pipe.core.edit A_040 B_010  every delivery of those shots
"""

from __future__ import annotations

import argparse
from datetime import datetime

from pipe.core.edit.delivery import Delivery, shot_deliveries
from pipe.core.shotgrid import Shot, ShotGrid
from pipe.core.util.paths import get_edit_path

# Summary columns, in the order work moves through the show.
_DEPARTMENTS = ("previs", "anim", "fx", "lighting")


def main() -> None:
    parser = argparse.ArgumentParser(description="List editorial deliveries.")
    parser.add_argument("shots", nargs="*", help="shot codes; all shots if none")
    args = parser.parse_args()

    from env_sg import DB_Config

    sg_shots = {s.code: s for s in ShotGrid.connect(DB_Config).find_shots()}
    if args.shots:
        for code in args.shots:
            _print_shot(code, sg_shots.get(code))
    else:
        _print_summary(sg_shots)


def _print_shot(code: str, sg_shot: Shot | None) -> None:
    deliveries, strays = shot_deliveries(code)
    print(f"{code}  {_range(sg_shot)}")
    newest = {d.department: d for d in deliveries}
    for d in deliveries:
        mark = "*" if newest[d.department] is d else " "
        print(f"  {mark} {d.path.name:<32} {_modified(d)}")
    for path in strays:
        print(f"    not a delivery: {path.name}")
    if not deliveries and not strays:
        print(f"    no deliveries in {get_edit_path() / code}")


def _print_summary(sg_shots: dict[str | None, Shot]) -> None:
    folders = sorted(p.name for p in get_edit_path().iterdir() if p.is_dir())
    print(f"{'shot':<12} {'ShotGrid':<34} " + " ".join(f"{d:<9}" for d in _DEPARTMENTS))
    for code in folders:
        deliveries, _ = shot_deliveries(code)
        if not deliveries:
            continue
        newest = {d.department: d.version for d in deliveries}
        columns = " ".join(
            f"{'v%03d' % newest[d] if d in newest else '-':<9}" for d in _DEPARTMENTS
        )
        print(f"{code:<12} {_range(sg_shots.get(code)):<34} {columns}")


def _range(sg_shot: Shot | None) -> str:
    if sg_shot is None:
        return "not in ShotGrid or Out of Picture"
    if sg_shot.cut_in is None or sg_shot.cut_out is None:
        return "no cut range"
    length = sg_shot.cut_out - sg_shot.cut_in + 1
    return f"{sg_shot.cut_in}-{sg_shot.cut_out} ({length}f)"


def _modified(delivery: Delivery) -> str:
    stamp = datetime.fromtimestamp(delivery.path.stat().st_mtime)
    return stamp.strftime("%Y-%m-%d %H:%M")


if __name__ == "__main__":
    main()
