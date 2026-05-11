from __future__ import annotations

import hou
from env_sg import DB_Config

from core.shotgrid import ShotGrid


class AnimPostProcessor:
    _conn: ShotGrid

    def __init__(self):
        self._conn = ShotGrid.connect(DB_Config)

    def run(self, shot_code: str) -> None:
        # Set up
        shot = self._conn.get_shot(code=shot_code)
        shot_path = shot.shot_path
        cut_in, cut_out = shot.frame_range
        hou.playbar.setFrameRange(cut_in - 5, cut_out + 5)
        hou.playbar.setPlaybackRange(cut_in - 5, cut_out + 5)

        stage_ctx: hou.Node = hou.node("/stage")  # type: ignore

        # Linked Environment refs from `shot.sets` / `shot.set` arrive partial;
        # accessing `.environment_path` triggers the connection's lazy-fetch.
        # If the shot has no env at all, fall through with [None] so we always
        # build a single load-layer node — the post-process graph downstream
        # expects exactly one input.
        envs = shot.sets
        if not envs:
            sequence = shot.sequence
            envs = [shot.set or (sequence.set if sequence else None)]

        load_layers = []
        for env in envs:
            load_layer = stage_ctx.createNode("dbclark::main::Bobo_Load_Layers::1.0")
            load_layer.parm("shot").set(f"$JOB/{shot_path}")  # type: ignore
            for dep in ["cfx", "fx", "envfx", "flo", "lighting", "render"]:
                load_layer.parm(f"{dep}_enable").set(0)  # type: ignore
            if env and env.environment_path:
                load_layer.parm("layout_path").set(  # type: ignore
                    f"$JOB/{env.environment_path}/main.usd"
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
