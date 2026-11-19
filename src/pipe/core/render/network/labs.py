"""Which lab room each farm machine sits in, and where in the room.

Tractor groups machines by their profile, which follows their hardware, not
the room they're in; an artist looking for a machine wants the room, and the
desk. The rooms, their desks and who sits at each come from the lab's own
map (labMapper, by Samuel Schulthies), with machines named the way the farm
names them: cs-<asset tag>, or a pretty name. A desk may list a machine by
both, as waternoose is cs-1031635.
"""

from __future__ import annotations

from dataclasses import dataclass

# A desk on a room's plan is its number; a door is its words; None is floor.
Spot = int | str | None


@dataclass(frozen=True)
class Room:
    """A lab room: its label, its plan, and who sits where."""

    label: str
    # Rows of the plan, as the room is seen from above; empty without one.
    plan: tuple[tuple[Spot, ...], ...]
    # The machines at each desk, by its number or, in rooms without a plan,
    # by the words that say where it is.
    desks: dict[int | str, tuple[str, ...]]
    # Machines known to be in the room, but not at which desk.
    unplaced: tuple[str, ...] = ()


@dataclass(frozen=True)
class Place:
    """Where a machine is."""

    room: Room
    # Its desk on the plan, or the words for where it is, or None if unknown.
    desk: int | str | None


def _cs(*tags: str) -> tuple[str, ...]:
    return tuple(f"cs-{tag}" for tag in tags)


_DOOR_RIGHT, _DOOR_LEFT, _DOOR_UP = "Door →", "← Door", "Door ↑"

ROOMS = (
    Room(
        "1130 (Disney Lab)",
        (
            (None, None, None, None, None),
            (26, 27, None, 30, 31),
            (24, 25, None, 28, 29),
            (None, None, None, None, None),
            (20, 21, None, 22, 23),
            (18, 19, None, None, _DOOR_RIGHT),
            (None, None, None, None, None),
            (13, 14, None, 17, None),
            (11, 12, None, 15, 16),
            (None, None, None, None, None),
            (5, 6, None, 9, 10),
            (3, 4, None, 7, 8),
            (None, None, None, None, None),
            (1, None, None, None, 2),
        ),
        {
            1: ("waternoose", "cs-1031635"),
            **{
                desk: _cs(tag)
                for desk, tag in enumerate(
                    """1017524 1017452 1017461 1017449 1017463 1017443 1017533
                    1017459 1017462 1017454 1017525 1017448 1017446 1017518
                    1017522 1017520 1017442 1017456 1017451 1017457 1017523
                    1017453 1017444 1017532 1017528 1017445 1017450 1017447
                    1017527 1017458""".split(),
                    start=2,
                )
            },
        },
    ),
    Room(
        "1029 (Nintendo Lab)",
        (
            (None, None, None, None, None),
            (1, 2, None, 3, 4),
            (None, None, None, None, None),
            (5, 6, None, 7, 8),
            (None, None, None, None, None),
            (9, 10, None, 11, 12),
            (None, None, None, None, None),
            (_DOOR_LEFT, None, None, 13, 14),
            (None, None, None, None, None),
            (None, None, None, 17, 18),
            (None, None, None, None, None),
            (15, 16, None, 19, 20),
            (None, None, None, None, None),
            (21, 22, None, 23, 24),
            (None, None, None, None, None),
            (25, 26, None, 27, 28),
        ),
        {
            desk: (name,)
            for desk, name in enumerate(
                """fox samus ness captfalcon isabelle tomnook kkslider mrresetti
                dedede waddledee metaknight kirby oatchi pikmin zelda link olimar
                louie beedle epona impa ganon yoshi toad luigi mario cs-1017526
                peach""".split(),
                start=1,
            )
        },
    ),
    Room(
        "1138 (Pokemon Lab)",
        (
            (10, 9, 8, 7, _DOOR_RIGHT),
            (None, None, None, None, None),
            (None, None, 6, None, 1),
            (None, None, 5, None, 2),
            (11, None, 4, None, 3),
        ),
        {
            1: _cs("1017521"),
            2: ("bowser", "cs-1002502"),
            3: _cs("1017455"),
            4: _cs("1002512"),
            5: _cs("1017530"),
            6: _cs("1017519"),
            7: _cs("1017535"),
            8: _cs("1017529"),
            9: _cs("1002535"),
            10: ("randall", "cs-1031634"),
            11: _cs("1017534"),
        },
    ),
    Room(
        "1102 (Pre-Major Lab)",
        (
            (_DOOR_UP, None, None, 13, None, 12),
            (None, None, None, None, None, None),
            (1, None, 5, 6, None, 11),
            (2, None, None, 7, None, 10),
            (3, None, 4, 8, None, 9),
        ),
        {
            1: _cs("1002542"),
            2: _cs("1002550"),
            3: _cs("1002528"),
            4: _cs("1002536"),
            7: _cs("1002558"),
            8: _cs("1002555"),
            9: _cs("1002526"),
            10: _cs("1002511"),
            11: _cs("1002537"),
            12: _cs("1002527"),
        },
        # In the lab's list of 1102, but not on its map; desks 5, 6 and 13
        # are unnamed there.
        _cs("1002533", "1002541", "1002554", "1002525", "1002530", "1002532"),
    ),
    Room(
        "1110 (Theater)",
        (),
        {
            "at the front of the theater": _cs("1017536"),
            "at the back of the theater": ("yzma",),
        },
    ),
    Room(
        "2255 (Parris' Lab)",
        (),
        {
            "next to the main door": ("maleficent", "ursula"),
            "at the desk between the thermostat and the Epson printer": _cs(
                "1002523", "1002548"
            ),
            "on the east wall, at the back": (
                "cruella",
                "scar",
                "hook",
                "hades",
                "cs-teamcity1",
                *_cs("1002531", "1002549", "1002547", "1002540", "1002534"),
                *_cs("1002545", "1002551", "1002524"),
            ),
        },
    ),
)

_PLACES = {
    machine: Place(room, desk)
    for room in ROOMS
    for desk, machines in room.desks.items()
    for machine in machines
} | {machine: Place(room, None) for room in ROOMS for machine in room.unplaced}
# For machines no list has.
UNLISTED = "Other"


def place_of(machine: str) -> Place | None:
    """Where `machine` is, or None if no list has it."""
    return _PLACES.get(machine.split(".")[0])


def lab_of(machine: str) -> str:
    """The label of the room `machine` is in, or `UNLISTED`."""
    place = place_of(machine)
    return place.room.label if place else UNLISTED
