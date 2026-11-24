"""The Render Network window's headless Houdini: one command, one JSON answer.

The window runs it through the pipeline launcher, so it has every Houdini
session's environment:

    python -m pipe houdini -p worker.py describe --shot A_080 --result out.json

See `pipe.core.render.protocol` for the commands and the answer.
"""

from __future__ import annotations

import argparse
import logging
import sys
import traceback
from pathlib import Path

import hou

from pipe.core.render.protocol import (
    COMMANDS,
    DESCRIBE,
    LIGHTING_HIP,
    LightingTarget,
    PUBLISH_LIGHTING,
    SEND,
    Edits,
    render_hip,
    shot_folder,
    Failure,
    LightingChoice,
    Result,
    Sent,
)
from pipe.core.publish import Refused
from pipe.core.render.lighters import NotTheLighter, check_lighter
from pipe.core.shot import shot_target
from pipe.core.shotgrid import Shot, ShotGrid, ShotGridError
from pipe.core.ui.publish_dialog import PublishChoice
from pipe.dcc.houdini.publish import version
from pipe.dcc.houdini.render_network import scene, schema
from pipe.dcc.houdini.tractor import SendRefused, render_send
from pipe.dcc.houdini.tractor.send import BUILD_FAILED

# The department folder and publish of a shot's lighting.
LIGHTING = "lighting"
UNEXPECTED = (
    "Something unexpected went wrong in the headless Houdini, so nothing was "
    "saved or sent. Send the details below to a TD."
)

log = logging.getLogger(__name__)


class WorkerRefused(Exception):
    """Why the worker did nothing, worded for the artist."""


def main(argv: list[str]) -> None:
    args = _parse(argv)
    result = Result(shot=args.shot)
    try:
        if args.command == PUBLISH_LIGHTING:
            publish_lighting(args.shot, args.choice, args.hip, result)
        else:
            run(args.command, args.shot, args.values, args.hip, result)
    except (
        WorkerRefused,
        scene.SceneRefused,
        SendRefused,
        Refused,
        NotTheLighter,
    ) as refusal:
        result.failure = Failure(str(refusal))
    except render_send.BuildFailed as failure:
        result.failure = Failure(BUILD_FAILED, failure.details)
    except Exception:
        log.exception("The Render Network worker failed: %s", argv)
        result.failure = Failure(UNEXPECTED, traceback.format_exc())
    result.write(args.result)


def run(
    command: str, shot_code: str, values: Path | None, hip: Path | None, result: Result
) -> None:
    """Open the shot's render hip, and save and send it as `command` asks,
    filling in `result` as each step finishes."""
    shot = _shot(_connect(), shot_code)
    opened = scene.open_scene(shot, hip or render_hip(shot_code))
    network = opened.network
    result.hip = str(opened.hip)
    result.hip_existed = opened.existed
    result.seeded_from = opened.seeded_from
    result.notes = opened.notes
    interface = schema.describe(network)

    if command != DESCRIBE:
        if values is None:
            raise WorkerRefused("The window sent no values to save.")
        edits = Edits.read(values)
        scene.apply_departments(edits.departments)
        schema.apply_values(network, interface, edits.values)
        scene.save(opened)
    if command == SEND:
        scene.check_departments()
        result.sent = _send(network)
        # Send wrote each layer's Last Job and next Output Folder.
        scene.save(opened)

    result.schema = interface
    result.values = schema.read_values(network, interface)
    result.evaluated = schema.evaluate_expressions(network, interface)
    result.layers = scene.layers(network)
    result.departments = scene.departments(network)
    result.prims = scene.prims(network)
    result.lighting_target = _lighting_target(shot)


def publish_lighting(
    shot_code: str, choice_path: Path | None, hip: Path | None, result: Result
) -> None:
    """Publish the shot's lighting hip, as last saved, as its next lighting
    version, the way its publish node's button does. Only its lighter may."""
    if choice_path is None:
        raise WorkerRefused(
            "The window sent no publish note, so nothing was published."
        )
    conn = _connect()
    check_lighter(conn, shot_code)
    lighting = (hip or shot_folder(shot_code) / LIGHTING_HIP).resolve()
    if not lighting.is_file():
        raise WorkerRefused(
            f"{shot_code} has no lighting hip at {lighting}, so there is nothing "
            "to publish. Open the shot's lighting in Houdini first."
        )
    scene.load(lighting)
    target = version.hip_target(conn, lighting)
    choice = LightingChoice.read(choice_path)
    lines = version.publish_hip(
        conn,
        target,
        lighting,
        PublishChoice(
            note=choice.note,
            announce=choice.announce,
            final=choice.final,
            playblast=False,
        ),
    )
    # A shot's publish never asks anything, so it never backs out.
    result.published = lines or []
    result.hip = str(lighting)


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
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--shot", required=True, help="the shot's code, e.g. A_080")
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--values", type=Path, help="values to save, for save and send")
    parser.add_argument(
        "--choice", type=Path, help="the publish dialog's choices, for publish-lighting"
    )
    parser.add_argument(
        "--hip",
        type=Path,
        help="use this hip instead of the shot's render hip, or its lighting hip",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] [%(levelname)s] %(message)s",
    )
    main(sys.argv[1:])
