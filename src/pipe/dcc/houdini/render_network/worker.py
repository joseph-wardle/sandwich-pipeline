"""Rancher's Houdini: a shot's render hip, open until the window closes it.

The window runs it through the pipeline launcher, so it has every Houdini
session's environment:

    python -m pipe houdini -p worker.py --shot A_080 --socket /tmp/rancher_…

See `pipe.core.render.protocol` for the requests and replies.
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import hou

from pipe.core.publish import Refused
from pipe.core.render.lighters import NotTheLighter, check_lighter
from pipe.core.render.protocol import (
    COMMAND,
    LIGHTING_HIP,
    OPEN,
    PUBLISH_LIGHTING,
    REMOVE_INSTANCE,
    REVERT,
    SAVE,
    SEND,
    SET_DEPARTMENT,
    SET_PARM,
    Failure,
    LightingTarget,
    Reply,
    Sent,
    render_hip,
    shot_folder,
)
from pipe.core.shot import shot_target
from pipe.core.shotgrid import Shot, ShotGrid, ShotGridError
from pipe.core.ui.publish_dialog import PublishChoice
from pipe.dcc.houdini.publish.version import hip_target, publish_hip
from pipe.dcc.houdini.render_network import scene, schema
from pipe.dcc.houdini.tractor import SendRefused, render_send
from pipe.dcc.houdini.tractor.send import BUILD_FAILED

LIGHTING = "lighting"
UNEXPECTED = (
    "Something unexpected went wrong in Houdini, so that wasn't done. Send the "
    "details below to a TD."
)

log = logging.getLogger(__name__)


class WorkerRefused(Exception):
    """Why the worker did nothing, worded for the artist."""


class Worker:
    """The requests the window sends, each done to the open render hip."""

    _shot_code: str
    _hip: Path
    _conn: ShotGrid | None
    _shot: Shot | None
    _scene: scene.RenderScene | None

    def __init__(self, shot_code: str, hip: Path) -> None:
        self._shot_code, self._hip = shot_code, hip
        self._conn, self._shot, self._scene = None, None, None

    def open(self) -> Reply:
        """Load the render hip from disk, dropping any unsaved edits."""
        self._conn = self._conn or _connect()
        self._shot = _shot(self._conn, self._shot_code)
        self._scene = scene.open_scene(self._shot, self._hip)
        network = self._scene.network
        return Reply(
            hip=str(self._scene.hip),
            hip_existed=self._scene.existed,
            seeded_from=self._scene.seeded_from,
            notes=self._scene.notes,
            schema=schema.describe(network),
            parms=schema.states(network),
            prims=scene.prims(network),
            layers=scene.layers(network),
            departments=scene.departments(network),
            lighting_target=_lighting_target(self._shot),
        )

    def set_parm(self, name: str, value: list[Any]) -> Reply:
        network = self._opened().network
        schema.set_parm(network, name, value)
        return Reply(parms=schema.states(network))

    def remove_instance(self, name: str, position: int) -> Reply:
        network = self._opened().network
        schema.remove_instance(network, name, position)
        return Reply(parms=schema.states(network))

    def revert(self, names: list[str]) -> Reply:
        network = self._opened().network
        schema.revert(network, names)
        return Reply(parms=schema.states(network))

    def set_department(self, row: str, enabled: bool, version: str) -> Reply:
        network = self._opened().network
        scene.set_department(row, enabled, version)
        return Reply(departments=scene.departments(network), prims=scene.prims(network))

    def save(self) -> Reply:
        opened = self._opened()
        scene.save(opened)
        return Reply(hip=str(opened.hip), hip_existed=True)

    def send(self) -> Reply:
        opened = self._opened()
        scene.check_departments()
        scene.save(opened)
        sent = _send(opened.network)
        # Send wrote each layer's Last Job and next Output Folder.
        scene.save(opened)
        return Reply(
            hip=str(opened.hip),
            hip_existed=True,
            parms=schema.states(opened.network),
            layers=scene.layers(opened.network),
            sent=sent,
        )

    def publish_lighting(self, note: str, announce: bool, final: bool) -> Reply:
        """Publish the shot's lighting hip, as last saved, as its next lighting
        version, the way its publish node's button does. Only its lighter may."""
        conn = self._conn = self._conn or _connect()
        check_lighter(conn, self._shot_code)
        lighting = shot_folder(self._shot_code) / LIGHTING_HIP
        if not lighting.is_file():
            raise WorkerRefused(
                f"{self._shot_code} has no lighting hip at {lighting}, so there is "
                "nothing to publish. Open the shot's lighting in Houdini first."
            )
        try:
            scene.load(lighting)
            target = hip_target(conn, lighting)
            choice = PublishChoice(
                note=note, announce=announce, final=final, playblast=False
            )
            published = publish_hip(conn, target, lighting, choice)
        finally:
            # The render hip reads lighting's publish, so it opens again either way.
            reply = self.open()
        reply.published = published or []
        return reply

    def _opened(self) -> scene.RenderScene:
        if self._scene is None:
            raise WorkerRefused("The render hip isn't open. Reload the shot.")
        return self._scene


def main(argv: list[str]) -> None:
    args = _parse(argv)
    worker = Worker(args.shot, args.hip or render_hip(args.shot))
    commands: dict[str, Callable[..., Reply]] = {
        OPEN: worker.open,
        SET_PARM: worker.set_parm,
        REMOVE_INSTANCE: worker.remove_instance,
        REVERT: worker.revert,
        SET_DEPARTMENT: worker.set_department,
        SAVE: worker.save,
        SEND: worker.send,
        PUBLISH_LIGHTING: worker.publish_lighting,
    }
    with socket.socket(socket.AF_UNIX) as connection:
        connection.connect(args.socket)
        stream = connection.makefile("rw", encoding="utf-8")
        # Ends when the window closes the socket.
        for line in stream:
            request = json.loads(line)
            name = request.pop(COMMAND)
            stream.write(_answer(name, commands[name], request).to_line())
            stream.flush()


def _answer(name: str, command: Callable[..., Reply], args: dict[str, Any]) -> Reply:
    try:
        return command(**args)
    except (
        WorkerRefused,
        scene.SceneRefused,
        SendRefused,
        Refused,
        NotTheLighter,
    ) as refusal:
        return Reply(failure=Failure(str(refusal)))
    except render_send.BuildFailed as failure:
        return Reply(failure=Failure(BUILD_FAILED, failure.details))
    except Exception:
        log.exception("Houdini failed to %s with %s", name, args)
        return Reply(failure=Failure(UNEXPECTED, traceback.format_exc()))


def _lighting_target(shot: Shot) -> LightingTarget | None:
    try:
        target = shot_target(shot, LIGHTING)
    # Only Publish Lighting needs it; the rest of the window works without it.
    except Exception:
        log.warning(
            "Could not read %s's next lighting version", shot.code, exc_info=True
        )
        return None
    return LightingTarget(
        name=target.name,
        current=str(target.current),
        version=target.version,
        file_name=target.file_name,
        file_code=target.file_code,
        department=target.department,
        final=target.final,
    )


def _send(network: hou.LopNode) -> Sent:
    chosen = scene.enabled_layers(network)
    if not chosen:
        raise WorkerRefused(
            "No layers are turned on, so nothing was sent. Turn on at least one "
            "layer, then Send again."
        )
    sent = render_send.send_job(scene.submit(network), chosen)
    return Sent(
        title=sent.title,
        folders=[str(f) for f in sent.folders],
        job_id=sent.job_id,
        unconfirmed=sent.unconfirmed,
    )


def _connect() -> ShotGrid:
    # env_sg holds gitignored production credentials; import lazily so
    # importing this module never requires them.
    from env_sg import DB_Config

    return ShotGrid.connect(DB_Config)


def _shot(conn: ShotGrid, code: str) -> Shot:
    try:
        return conn.get_shot(code=code)
    except ShotGridError as exc:
        raise WorkerRefused(
            f"Could not read the shot {code} from ShotGrid ({exc}). Check your "
            "network connection, then try again."
        ) from exc


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shot", required=True, help="the shot's code, e.g. A_080")
    parser.add_argument("--socket", required=True, help="the window's local socket")
    parser.add_argument(
        "--hip", type=Path, help="open this hip instead of the shot's render hip"
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] [%(levelname)s] %(message)s",
    )
    main(sys.argv[1:])
