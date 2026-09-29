"""Send: one Tractor job for every Configure → Denoise → Encode chain wired into Submit.

Send claims each chain's version folder, writes `render.usd` into it with every
output's path, reads what the job needs from that file, and spools the job.
"""

from __future__ import annotations

import logging
import os
import re
import traceback
from dataclasses import dataclass
from pathlib import Path

import hou
import tractor.api.author as author
from pxr import Sdf, Tf, Usd, UsdRender

from pipe.core.playblast.presets import FFmpegPreset
from pipe.dcc.houdini.tractor import SendRefused, denoise, folders, job, paths

CONFIGURE = "tractor_configure"
DENOISE = "tractor_denoise"
ENCODE = "tractor_encode_video"

ENGINE = "tractor-engine.cs.byu.edu"
ENGINE_PORT = 443
LICENSE_SERVER = "animlic.cs.byu.edu"
# Sent with the job so the blades find the same Houdini, RenderMan and colour
# config as the artist's session.
ENV_VARS = (
    "HOUDINI_PATH",
    "OCIO",
    "PATH",
    "PIXAR_LICENSE_FILE",
    "PXR_AR_DEFAULT_SEARCH_PATH",
    "PXR_PLUGINPATH_NAME",
    "RFHTREE",
    "RMAN_COLOR_CONFIG_DIR",
    "RMAN_PROCEDURALPATH",
    "RMANTREE",
)
# husk writes the image so far this often while it renders.
SNAPSHOT_SECONDS = 300

# Task titles join the layer to other words with spaces, and Tractor finds
# tasks by title, so a layer with a space could take another layer's title.
LAYER_NAME = re.compile(r"[A-Za-z0-9_-]+")

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Chain:
    configure: hou.LopNode
    denoise: hou.Node | None
    encode: hou.Node | None
    # Wired into Submit; render.usd holds its stage.
    output: hou.Node


def send(submit: hou.Node, inputs: list[hou.Node] | None = None) -> None:
    """Send `inputs`, by default every chain wired into `submit`, as one job."""
    try:
        chains = [chain(n) for n in (submit.inputs() if inputs is None else inputs)]
        check_layers(chains)
        folders.check_saved()
        claimed = folders.claim([c.configure for c in chains])
    except SendRefused as refusal:
        _refuse(refusal)
        return

    title = _text(submit, "title")
    try:
        built = build(submit, chains)
    except SendRefused as refusal:
        folders.release(claimed)
        _refuse(refusal)
        return
    except Exception:
        log.exception("Building the job from %s failed", submit.path())
        folders.release(claimed)
        hou.ui.displayMessage(
            "The job could not be built, so nothing was sent to Tractor. The "
            "details below say what went wrong.",
            title="Nothing was sent to Tractor",
            severity=hou.severityType.Error,
            details=traceback.format_exc(),
        )
        return

    try:
        job_id = built.spool(block=True)
    except Exception:
        # A lost reply looks the same as a refusal, and the job may already be
        # queued and reading its folders, so they stay.
        log.exception("Tractor did not confirm the job from %s", submit.path())
        job_id = None
        hou.ui.displayMessage(
            "Tractor did not confirm the job, but it may still have been queued. "
            f'Look for "{title}" at {ENGINE} before sending again.'
            "\n\nIts folders were kept:\n" + "\n".join(str(f) for f in claimed),
            title="Tractor did not confirm the job",
            severity=hou.severityType.Warning,
            details=traceback.format_exc(),
        )
    else:
        hou.ui.displayMessage(
            f"Job {job_id} renders into:\n"
            + "\n".join(str(folder) for folder in claimed)
            + f"\n\nVisit {ENGINE} to check progress.",
            title="Job sent to Tractor",
        )

    # Recorded after the dialog, which must reach the artist even when a locked
    # parm or take refuses the record.
    for c, folder in zip(chains, claimed):
        folders.record_sent(c.configure, folder, job_id)


def _refuse(refusal: SendRefused) -> None:
    hou.ui.displayMessage(
        str(refusal),
        title="Nothing was sent to Tractor",
        severity=hou.severityType.Error,
    )


def chain(node: hou.Node | None) -> Chain:
    """Walk up from a Submit input: Encode and Denoise are optional, Configure is not."""
    output = node
    found: dict[str, hou.Node] = {}
    for kind in (ENCODE, DENOISE, CONFIGURE):
        if node is not None and _kind(node) == kind:
            found[kind] = node
            node = node.input(0)
    configure = found.get(CONFIGURE)
    if output is None or not isinstance(configure, hou.LopNode):
        where = f" {output.path()}" if output is not None else ""
        raise SendRefused(
            f"Submit's input{where} is not a Tractor chain. Wire Submit to a "
            "Tractor Configure, with Denoise and then Encode between them if wanted."
        )
    return Chain(configure, found.get(DENOISE), found.get(ENCODE), output)


def check_layers(chains: list[Chain]) -> None:
    if not chains:
        raise SendRefused("Wire a Tractor Configure into Submit first.")
    names = [_text(c.configure, folders.LAYER) for c in chains]
    for c, name in zip(chains, names):
        if not LAYER_NAME.fullmatch(name):
            raise SendRefused(
                f'The layer "{name}" of {c.configure.path()} may use only letters, '
                "digits, underscores and hyphens. Rename it in Layer."
            )
        if names.count(name) > 1:
            raise SendRefused(
                f'More than one Submit input renders the layer "{name}". Wire each '
                "Configure into Submit once, and give each its own Layer."
            )


def build(submit: hou.Node, chains: list[Chain]) -> author.Job:
    author.setEngineClientParam(hostname=ENGINE, port=ENGINE_PORT)
    return job.build(
        _text(submit, "title"),
        int(submit.evalParm("priority")),
        _setenv(),
        [_layer(c) for c in chains],
    )


def _layer(chain: Chain) -> job.Layer:
    configure = chain.configure
    folder = Path(_text(configure, folders.OUTPUT))
    frames = _frames(configure)
    skips = frames != list(range(frames[0], frames[-1] + 1))
    if skips and (chain.denoise or chain.encode):
        raise SendRefused(
            f"{configure.path()} skips frames, but Denoise reads each frame's "
            "neighbours and Encode plays the frames back to back. Render every "
            "frame of the range, or remove Denoise and Encode."
        )
    _write_render_usd(configure, chain.output)

    stage = Usd.Stage.Open(str(folder / paths.RENDER_USD), Usd.Stage.LoadNone)
    settings = paths.rendered_settings(stage, _toggled(configure, "rendersettings"))
    _check_camera(configure, stage, settings)
    products = paths.products(settings)
    denoised = denoise.product(settings, products) if chain.denoise else None
    outputs = products + paths.cryptomattes(settings, products)
    written = paths.author_outputs(
        stage, frames, outputs, [denoised] if denoised else []
    )

    render = job.Render(husk=_husk(configure), folders=written)
    denoise_spec = None
    if denoised:
        denoise_spec = _write_denoise(folder, denoised)
    name = _text(configure, folders.LAYER)
    encode_spec = None
    if chain.encode:
        encode_spec = _encode(chain.encode, name, folder, settings, products, denoised)
    return job.Layer(name, folder, frames, render, denoise_spec, encode_spec)


def _check_camera(
    configure: hou.Node, stage: Usd.Stage, settings: UsdRender.Settings
) -> None:
    """husk fails every frame when Override Camera is no camera, and renders
    through a default camera, exiting 0, when the settings' camera is missing.
    With neither, it renders through the first camera it finds, or fails every
    frame when there is none."""
    override = _toggled(configure, "override_camera")
    targets = settings.GetCameraRel().GetForwardedTargets()
    path = Sdf.Path(override) if override else next(iter(targets), None)
    if path is None:
        raise SendRefused(
            f"Nothing chooses the camera for {configure.path()}. Turn on Override "
            f"Camera, or set the camera on {settings.GetPath()}."
        )
    if path.IsAbsolutePath():
        # A camera inside a payload exists only once the payload is loaded; Load
        # raises for a path under no prim at all.
        try:
            stage.Load(path, Usd.LoadWithoutDescendants)
        except Tf.ErrorException:
            pass
        prim = stage.GetPrimAtPath(path)
        if prim and prim.IsA(paths.CAMERA):
            return
    if override:
        raise SendRefused(
            f"Override Camera on {configure.path()} names {override}, which is not "
            "a camera on the stage. Correct it, or turn it off."
        )
    raise SendRefused(
        f"{settings.GetPath()} renders through {path}, which is not a camera on "
        "the stage. Correct the camera on the render settings, or set Override "
        f"Camera on {configure.path()}."
    )


def _write_render_usd(configure: hou.Node, output: hou.Node) -> None:
    # Inside a locked asset such as SKD Lookdev the path is authored already,
    # and setting it would raise a permission error.
    if hou.node(_text(configure, "rop_lop")) != output:
        configure.setParms({"rop_lop": output.path()})
    # Configure's contents always hold this ROP and its button.
    rop = configure.node("usd_rop")
    rop.parm("execute").pressButton()  # ty: ignore[unresolved-attribute]
    # A failed write shows in errors(); the button doesn't raise.
    if errors := rop.errors():  # ty: ignore[unresolved-attribute]
        raise SendRefused(
            f"{configure.path()} could not write render.usd:\n\n" + "\n".join(errors)
        )


def _frames(configure: hou.LopNode) -> list[int]:
    trange = _text(configure, "trange")
    if trange == "off":
        return [int(hou.frame())]
    if trange == "stage":
        stage = configure.stage()
        return list(
            range(int(stage.GetStartTimeCode()), int(stage.GetEndTimeCode()) + 1)
        )
    start, end, step = (int(v) for v in configure.evalParmTuple("f"))
    return list(range(start, end + 1, step))


def _husk(configure: hou.Node) -> list[str]:
    words = [
        *("--renderer", _text(configure, "renderer")),
        *("--purpose", "geometry,render"),
        *("--complexity", "veryhigh"),
        *("--verbose", "acet"),
        *("--snapshot", str(SNAPSHOT_SECONDS)),
    ]
    if camera := _toggled(configure, "override_camera"):
        words += ["--camera", camera]
    if settings := _toggled(configure, "rendersettings"):
        words += ["--settings", settings]
    words.append("--disable-dummy-raster-product")
    return words


def _write_denoise(folder: Path, product: Usd.Prim) -> job.Denoise:
    """Write denoise.json and return what the denoise tasks need."""
    denoise.write_config(folder, denoise.config(denoise.var_names(product)))
    return job.Denoise(product=product.GetName())


def _encode(
    node: hou.Node,
    layer: str,
    folder: Path,
    settings: UsdRender.Settings,
    products: list[Usd.Prim],
    denoised: Usd.Prim | None,
) -> job.Encode:
    if denoised:
        # denoise_batch writes the finished beauty as R, G, B.
        product, channels = denoised, ["R,G,B"]
    else:
        product = paths.beauty_product(settings, products)
        name = paths.beauty(product)
        # RenderMan names the beauty after its var, or R, G, B when it is
        # first; a var named in the frame is the surer match.
        channels = [f"{name}.r,{name}.g,{name}.b", f"{name}.R,{name}.G,{name}.B"]
        channels.append("R,G,B")
    preset = FFmpegPreset[_text(node, "preset")]
    video = _text(node, "output_file") or str(folder / f"{layer}.{preset.ext}")
    if Path(video).suffix != f".{preset.ext}":
        raise SendRefused(
            f"The Preset of {node.path()} makes a .{preset.ext} movie, but its "
            f"Output File is {video}. Change its extension, or clear Output File."
        )
    remove_frames = bool(node.evalParm("remove_frames"))
    # Only a movie in the new version folder can be this job's, so Cleanup
    # can trust it before deleting the frames it was made from.
    if remove_frames and Path(video).resolve().parent != folder.resolve():
        raise SendRefused(
            f"{node.path()} writes its movie to {video}, outside {folder}. With "
            "Remove Encoded Frames on, the frames would be deleted without proof "
            "that this job made the movie. Clear Output File, or turn off Remove "
            "Encoded Frames."
        )
    return job.Encode(
        images=folder / product.GetName(),
        channels=channels,
        video=Path(video),
        # A static method the stubs declare as an instance one.
        colorconfig=hou.Color.ocio_configPath(),  # ty: ignore[missing-argument]
        display=_text(node, "display"),
        view=_text(node, "view"),
        preset=preset,
        frame_rate=hou.fps(),
        remove_frames=remove_frames,
    )


def _setenv() -> str:
    return " ".join(
        ["setenv"]
        + [f"{name}={os.getenv(name)}" for name in ENV_VARS]
        + [f"HOUDINI_LICENSE_SERVER={LICENSE_SERVER}"]
    )


def _kind(node: hou.Node) -> str:
    return node.type().nameComponents()[2]


def _text(node: hou.Node, name: str) -> str:
    return str(node.evalParm(name))


def _toggled(node: hou.Node, name: str) -> str:
    """A parm's value when its checkbox is on, else empty."""
    return _text(node, name) if node.evalParm(f"toggle_{name}") else ""
