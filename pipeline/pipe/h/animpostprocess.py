from __future__ import annotations

from typing import TYPE_CHECKING, cast

import hou
from env_sg import DB_Config

from pipe.db import DB
from pipe.struct.db import EnvironmentStub, SequenceStub

if TYPE_CHECKING:
    pass


class AnimPostProcessor:
    _conn: DB

    def __init__(self):
        self._conn = DB(DB_Config)

    def run(self, shot_code: str) -> None:
        # Set up
        shot = self._conn.get_shot_by_code(shot_code)
        shot_path = shot.shot_path
        hou.playbar.setFrameRange(shot.cut_in - 5, shot.cut_out + 5)
        hou.playbar.setPlaybackRange(shot.cut_in - 5, shot.cut_out + 5)

        stage_ctx: hou.Node = hou.node("/stage")  # type: ignore

        load_layers = []
        sets = shot.sets
        if sets:
            for env_stub in sets:
                layout = self._conn.get_env_by_stub(env_stub)
                load_layer = stage_ctx.createNode(
                    "dbclark::main::Bobo_Load_Layers::1.0"
                )
                load_layer.parm("shot").set(f"$JOB/{shot_path}")  # type: ignore
                for dep in ["cfx", "fx", "envfx", "flo", "lighting", "render"]:
                    load_layer.parm(f"{dep}_enable").set(0)  # type: ignore
                if layout and layout.environment_path:
                    load_layer.parm("layout_path").set(  # type: ignore
                        f"$JOB/{layout.environment_path}/main.usd"
                    )
                load_layers.append(load_layer)
        else:
            # Fallback to single set logic
            env_stub = cast(
                EnvironmentStub,
                shot.set
                or self._conn.get_sequence_by_stub(
                    cast(SequenceStub, shot.sequence)
                ).set,
            )
            layout = self._conn.get_env_by_stub(env_stub)
            load_layer = stage_ctx.createNode("dbclark::main::Bobo_Load_Layers::1.0")
            load_layer.parm("shot").set(f"$JOB/{shot_path}")  # type: ignore
            for dep in ["cfx", "fx", "envfx", "flo" "lighting", "render"]:
                load_layer.parm(f"{dep}_enable").set(0)  # type: ignore
            if layout and layout.environment_path:
                load_layer.parm("layout_path").set(  # type: ignore
                    f"$JOB/{layout.environment_path}/main.usd"
                )
            load_layers.append(load_layer)

        # Merge load layers if there are multiple
        if len(load_layers) > 1:
            merge_node = stage_ctx.createNode("merge")
            for idx, layer in enumerate(load_layers):
                merge_node.setInput(idx, layer)
            input_node = merge_node
        else:
            input_node = load_layers[0]

        layer_break = stage_ctx.createNode("layerbreak")
        layer_break.setInput(0, input_node)

        postprocess = stage_ctx.createNode("sdm222::lnd_anim_postprocess::1.0")
        postprocess.setInput(0, layer_break)

        publish = stage_ctx.createNode("usd_rop")
        publish.parm("trange").set("normal")  # type: ignore
        publish.parm("lopoutput").set(f"$JOB/{shot_path}/anim/usd/post-process.usd")  # type: ignore
        publish.parm("savestyle").set("flattenalllayers")  # type: ignore
        publish.setInput(0, postprocess)

        publish.parm("execute").pressButton()  # type: ignore
