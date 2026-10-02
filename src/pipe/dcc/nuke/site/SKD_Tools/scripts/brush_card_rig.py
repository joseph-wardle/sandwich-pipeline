"""Load button for the BrushCardScene group (toolsets/brush_card_scene.nk).

The group merges the shot camera (centimeters, as published), the shot's sets
(meters, scaled x100 inside the group) and the chained BrushCards, then renders
only the cards through the camera. Viewing its `merge` node in 3D shows the
sets for placing cards.
"""

from __future__ import annotations

from pathlib import Path

import nuke
from env_sg import DB_Config

from pipe.core.sets import current_layer_path
from pipe.core.shotgrid import ShotGrid, ShotGridError
from pipe.core.util.paths import get_production_path

SET_SLOTS = 3


def shot_code_from_script() -> str | None:
    """`<prod>/shot/<code>/comp/<file>.nk` -> `<code>`."""
    parts = Path(nuke.root().name()).parts
    if "shot" in parts and len(parts) > parts.index("shot") + 1:
        return parts[parts.index("shot") + 1]
    return None


def set_layers(code: str) -> list[Path]:
    """The current layer of every set ShotGrid assigns the shot."""
    shot = ShotGrid.connect(DB_Config).get_shot(code=code)
    return [current_layer_path(set.name) for set in shot.sets or []]


def load() -> None:
    """Fill the camera and set paths; tell the artist about anything missing.

    The camera path does not need ShotGrid, so it is filled even when the set
    lookup fails. On a failed lookup the set slots keep their values, so paths
    an artist typed in are not lost.
    """
    node = nuke.thisNode()
    code = node["shot"].value() or shot_code_from_script()
    if not code:
        nuke.message(
            f"{node.Class()}: save the script in a shot folder or fill in 'shot'."
        )
        return
    node["shot"].setValue(code)

    problems = [_load_camera(node, code)]
    try:
        layers = set_layers(code)
    except ShotGridError as exc:
        problems.append(
            f"Couldn't look up {code}'s sets in ShotGrid, so the set paths were "
            f"left as they were. Fill them in by hand.\n\n{exc}"
        )
    else:
        problems += _load_sets(node, code, layers)

    found = [p for p in problems if p]
    if found:
        nuke.message(f"{node.Class()} ({code}):\n\n" + "\n\n".join(found))


def _load_camera(node: nuke.Node, code: str) -> str | None:
    camera = get_production_path() / "shot" / code / "cam" / "cam.usd"
    node["cam_file"].setValue(camera.as_posix())
    if not camera.is_file():
        return f"No camera has been published for {code}:\n{camera}"
    return None


def _load_sets(node: nuke.Node, code: str, layers: list[Path]) -> list[str]:
    problems: list[str] = []
    # A set linked in ShotGrid but never published has no layer yet, and an
    # empty slot renders fine while a missing file fails the render.
    published = [layer for layer in layers if layer.is_file()]
    for layer in layers:
        if layer not in published:
            problems.append(f"Set not published yet, skipped:\n{layer}")
    if not layers:
        problems.append(
            f"ShotGrid has no sets linked to {code}. Fill in the set paths by hand."
        )
    if len(published) > SET_SLOTS:
        skipped = "\n".join(p.as_posix() for p in published[SET_SLOTS:])
        problems.append(
            f"{code} has {len(published)} sets; only the first {SET_SLOTS} "
            f"were loaded. Skipped:\n{skipped}"
        )
    for i in range(SET_SLOTS):
        path = published[i].as_posix() if i < len(published) else ""
        node[f"set_file{i + 1}"].setValue(path)
    return problems
