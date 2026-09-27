from __future__ import annotations

from pipe.core.shotgrid import Environment, Set, Shot


def linked_environments(shot: Shot) -> list[Environment]:
    """The shot's sets, falling back to the one linked on its sequence."""
    envs = [env for env in (shot.sets or []) if env is not None]
    if envs:
        return envs
    sequence = shot.sequence
    sole_env = shot.set or (sequence.set if sequence else None)
    return [sole_env] if sole_env else []


def assigned_sets(shot: Shot) -> list[Set]:
    """The sets ShotGrid assigns to the shot, in its `sg_sets` order."""
    return [Set(id=link.id, code=link.code) for link in shot.sets or []]
