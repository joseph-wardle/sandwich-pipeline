from __future__ import annotations

from pipe.core.shotgrid import Environment, Shot


def linked_environments(shot: Shot) -> list[Environment]:
    """The shot's sets, falling back to the one linked on its sequence."""
    envs = [env for env in (shot.sets or []) if env is not None]
    if envs:
        return envs
    sequence = shot.sequence
    sole_env = shot.set or (sequence.set if sequence else None)
    return [sole_env] if sole_env else []
