from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import maya.cmds as mc

if TYPE_CHECKING:
    from typing import Any, Generator, Literal

log = logging.getLogger(__name__)


@dataclass
class HudDefinition:
    """
    Definition for a viewport HUD.
    Attributes
        name: str
            Internal name used by Maya for the HUD
        command: Callable[[], str]
            Command for the HUD to call
        section: int
            HUD section to occupy (see Maya docs)
        label: str
            String that precedes the return value of `command`
        event: str
            Event string that triggers a refresh (see Maya docs)
        idle_refresh: bool
            Alternative to `event`, will refresh every frame
        blockSize: Literal["small", "large"]
            Amount of HUD space to occupy
        labelFontSize: Literal["small", "large"]
    """

    name: str
    command: Callable[[], str]
    section: int
    label: str = ""
    event: str = ""
    idle_refresh: bool = False
    blockSize: Literal["small", "large"] = "small"
    labelFontSize: Literal["small", "large"] = "small"


@contextmanager
def applied_hud(
    builtin_huds: list[str], custom_huds: list[HudDefinition]
) -> Generator[None, None, None]:
    """Show a curated set of HUDs while a playblast captures, then restore."""
    # hide current huds and store current state
    orig_visibility: dict[str, bool] = {}
    orig_huds: list[str] = mc.headsUpDisplay(query=True, listHeadsUpDisplays=True)  # type: ignore
    for hud in orig_huds:
        vis = bool(mc.headsUpDisplay(hud, query=True, visible=True))
        orig_visibility[hud] = vis
        if vis:
            mc.headsUpDisplay(hud, edit=True, visible=False)

    # display requested builtin huds
    for hud in builtin_huds:
        mc.headsUpDisplay(hud, edit=True, visible=True)

    # create requested custom huds
    for chud in custom_huds:
        if chud.name in orig_huds:
            mc.headsUpDisplay(chud.name, remove=True)

        kwargs: dict[str, Any] = dict()
        if chud.idle_refresh:
            kwargs.update({"attachToRefresh": True})
        else:
            kwargs.update({"event": chud.event})

        mc.headsUpDisplay(
            chud.name,
            block=mc.headsUpDisplay(nextFreeBlock=chud.section),  # type: ignore
            blockSize=chud.blockSize,
            command=chud.command,
            label=chud.label,
            labelFontSize=chud.labelFontSize,
            section=chud.section,
            **kwargs,
        )

    try:
        yield
    finally:
        # restore original visibility
        for hud, state in orig_visibility.items():
            mc.headsUpDisplay(hud, edit=True, visible=state)

        for chud in custom_huds:
            mc.headsUpDisplay(chud.name, remove=True)


__all__ = [
    "HudDefinition",
    "applied_hud",
]
