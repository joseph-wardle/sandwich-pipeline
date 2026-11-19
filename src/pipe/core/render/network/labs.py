"""Which lab room each farm machine sits in, as the lab's own list has them.

Tractor groups machines by their profile, which follows their hardware, not
the room they're in; an artist looking for a machine wants the room.
"""

from __future__ import annotations

_LABS = {
    "1130 (Disney Lab)": """
        cs-1017528 cs-1017445 cs-1017444 cs-1017532 cs-1017527 cs-1017458
        cs-1017450 cs-1017447 cs-1017451 cs-1017457 cs-1017442 cs-1017456
        cs-1017523 cs-1017453 cs-1017448 cs-1017446 cs-1017454 cs-1017525
        cs-1017520 cs-1017518 cs-1017522 cs-1017449 cs-1017463 cs-1017452
        cs-1017461 cs-1017459 cs-1017462 cs-1017443 cs-1017533 cs-1031635
        cs-1017524 waternoose
    """,
    "1029 (Nintendo Lab)": """
        fox samus ness captfalcon isabelle tomnook kkslider mrresetti dedede
        metaknight kirby oatchi pikmin zelda link olimar louie beedle epona
        impa ganon yoshi toad luigi mario peach cs-1017526
    """,
    "1138 (Pokemon Lab)": """
        cs-1031634 cruella cs-1017529 cs-1017535 cs-1017519 cs-1017530 hook
        cs-1017521 bowser cs-1017455 cs-1017534
    """,
    "1102 (Pre-Major Lab)": """
        cs-1002533 cs-1002527 cs-1002542 cs-1002541 cs-1002528 cs-1002554
        cs-1002536 cs-1002525 cs-1002558 cs-1002530 cs-1002537 cs-1002532
        cs-1002526
    """,
}
_LAB_OF = {machine: lab for lab, names in _LABS.items() for machine in names.split()}
# For machines the lab's list doesn't have.
UNLISTED = "Other"


def lab_of(machine: str) -> str:
    """The lab `machine` is in, or `UNLISTED`."""
    return _LAB_OF.get(machine.split(".")[0], UNLISTED)
