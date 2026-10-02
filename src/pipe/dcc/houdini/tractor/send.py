"""Send: one Tractor job for every Configure → Denoise → Encode chain wired into Submit.

Send claims each chain's version folder, writes `render.usd` into it with every
output's path and every publish it reads pinned, reads what the job needs from
that file, and spools the job.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import hou
import tractor.api.author as author
from pxr import Sdf, Tf, Usd, UsdRender

from pipe.core.playblast.presets import FFmpegPreset
from pipe.core.publish import current_version, loaded_version, pin
from pipe.core.render import RENDER_USD
from pipe.dcc.houdini.tractor import (
    SendRefused,
    commands,
    denoise,
    folders,
    job,
    parms,
    paths,
)

CONFIGURE = "tractor_configure"
DENOISE = "tractor_denoise"
ENCODE = "tractor_encode_video"

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
# Task titles join the layer to other words with spaces, and Tractor finds
# tasks by title, so a layer with a space could take another layer's title.
LAYER_NAME = re.compile(r"[A-Za-z0-9_-]+")
RENDERMAN = "HdPrman"

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
    claimed: list[Path] = []
    built = None
    try:
        chains = [chain(n) for n in (submit.inputs() if inputs is None else inputs)]
        check_layers(chains)
        folders.check_saved()
        check_parms(chains)
        claimed = folders.claim([c.configure for c in chains])
        title = _title(submit, chains)
        built = build(submit, title, chains)
    except SendRefused as refusal:
        _refuse(str(refusal))
    except Exception:
        log.exception("Building the job from %s failed", submit.path())
        _refuse(
            "The job could not be built, so nothing was sent to Tractor. The "
            "details below say what went wrong.",
            traceback.format_exc(),
        )
    if built is None:
        folders.release(claimed)
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
            f'Look for "{title}" in Tractor before sending again.'
            "\n\nIts folders were kept:\n" + "\n".join(str(f) for f in claimed),
            title="Tractor did not confirm the job",
            severity=hou.severityType.Warning,
            details=traceback.format_exc(),
        )
    else:
        hou.ui.displayMessage(
            f"Job {job_id} renders into:\n"
            + "\n".join(str(folder) for folder in claimed)
            + "\n\nCheck its progress in Tractor.",
            title="Job sent to Tractor",
        )

    for c, folder in zip(chains, claimed):
        folders.record_sent(c.configure, folder, job_id)


def _refuse(message: str, details: str | None = None) -> None:
    hou.ui.displayMessage(
        message,
        title="Nothing was sent to Tractor",
        severity=hou.severityType.Error,
        details=details,
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
    names = [parms.text(c.configure, parms.LAYER) for c in chains]
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


def check_parms(chains: list[Chain]) -> None:
    """Refuse what the parms alone show is wrong, before any folder is claimed."""
    for c in chains:
        root = parms.text(c.configure, parms.ROOT)
        # A relative one would be read from wherever Houdini was started.
        if not Path(root).is_absolute():
            raise SendRefused(
                f'Render Root on {c.configure.path()} is "{root}", which is not a '
                "full path. Start it with / or $HIP."
            )
        renderer = parms.text(c.configure, parms.RENDERER)
        if (c.denoise or c.encode) and RENDERMAN not in renderer:
            raise SendRefused(
                f"{c.configure.path()} renders with {renderer}, but Denoise and "
                "Encode read only RenderMan's frames. Remove them, or choose a "
                "RenderMan renderer on Configure."
            )
        frames = parms.frames(c.configure)
        skips = frames != list(range(frames[0], frames[-1] + 1))
        if skips and (c.denoise or c.encode):
            raise SendRefused(
                f"{c.configure.path()} skips frames, but Denoise reads each frame's "
                "neighbours and Encode plays the frames back to back. Render every "
                "frame of the range, or remove Denoise and Encode."
            )
        if c.encode:
            _movie(c.encode, c.configure)


def _title(submit: hou.Node, chains: list[Chain]) -> str:
    """Submit's Title, or one that tells the job apart in Tractor's list."""
    if title := parms.text(submit, "title"):
        return title
    # The last folders of a shot's lighting hip are <shot>/lighting.
    hip = Path(hou.text.expandString("$HIP"))
    layers = ", ".join(parms.text(c.configure, parms.LAYER) for c in chains)
    return f"{hip.parent.name} {hip.name}: {layers}"


def build(submit: hou.Node, title: str, chains: list[Chain]) -> author.Job:
    # Each current layer is read once, so every layer of the job renders the
    # same versions even when someone publishes during the Send.
    version_of = functools.cache(_sent_version)
    return job.build(
        title,
        int(submit.evalParm("priority")),
        _setenv(),
        [_layer(c, version_of) for c in chains],
    )


def _layer(chain: Chain, version_of: Callable[[Path], int | None]) -> commands.Layer:
    configure = chain.configure
    folder = Path(parms.text(configure, parms.OUTPUT))
    frames = parms.frames(configure)
    _write_render_usd(configure, chain.output)
    _pin(Sdf.Layer.FindOrOpen(str(folder / RENDER_USD)), version_of)

    stage = Usd.Stage.Open(str(folder / RENDER_USD), Usd.Stage.LoadNone)
    settings = paths.rendered_settings(stage, parms.toggled(configure, parms.SETTINGS))
    _check_camera(configure, stage, settings)
    products = paths.products(settings)
    denoised = denoise.product(settings, products) if chain.denoise else None
    written = _write_outputs(configure, settings, products, denoised, frames)

    encode = chain.encode
    return commands.Layer(
        parms.text(configure, parms.LAYER),
        folder,
        frames,
        commands.Render(husk=parms.husk(configure), folders=written),
        _write_denoise(folder, denoised) if denoised else None,
        _encode(encode, configure, settings, products, denoised) if encode else None,
    )


def _write_outputs(
    configure: hou.Node,
    settings: UsdRender.Settings,
    products: list[Usd.Prim],
    denoised: Usd.Prim | None,
    frames: list[int],
) -> list[Path]:
    """Write every output's path into render.usd, and return the folders husk writes."""
    renderer = parms.text(configure, parms.RENDERER)
    outputs = products + paths.cryptomattes(settings, products, renderer)
    stage = settings.GetPrim().GetStage()
    return paths.author_outputs(stage, frames, outputs, [denoised] if denoised else [])


def _check_camera(
    configure: hou.Node, stage: Usd.Stage, settings: UsdRender.Settings
) -> None:
    """husk fails every frame when Override Camera is no camera, and renders
    through a default camera, exiting 0, when the settings' camera is missing.
    With neither, it renders through the first camera it finds, or fails every
    frame when there is none."""
    override = parms.toggled(configure, parms.CAMERA)
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
    if hou.node(parms.text(configure, "rop_lop")) != output:
        configure.setParms({"rop_lop": output.path()})
    # Configure's contents always hold this ROP and its button.
    rop = configure.node("usd_rop")
    rop.parm("execute").pressButton()  # ty: ignore[unresolved-attribute]
    # A failed write shows in errors(); the button doesn't raise.
    if errors := rop.errors():  # ty: ignore[unresolved-attribute]
        raise SendRefused(
            f"{configure.path()} could not write render.usd:\n\n" + "\n".join(errors)
        )


def _pin(layer: Sdf.Layer, version_of: Callable[[Path], int | None]) -> None:
    """Pin `layer` and the layers the ROP wrote beside it.

    The job then renders the versions that were current at the Send, on every
    frame and every retry.
    """
    pin(layer, version_of)
    layer.Save()
    folder = Path(layer.realPath).parent
    for path in layer.GetCompositionAssetDependencies():
        written = Path(layer.ComputeAbsolutePath(path))
        if written.parent == folder:
            _pin(Sdf.Layer.FindOrOpen(str(written)), version_of)


def _sent_version(current: Path) -> int | None:
    """The version of `current` the job renders, which is the one on disk.

    Raises:
        SendRefused: The hip shows another version. The job would render one
            the artist hasn't seen, under whatever this hip authored on top of
            the other.
    """
    version = current_version(current)
    loaded = loaded_version(current)
    if version is None or loaded is None or loaded == version:
        return version
    raise SendRefused(
        f"{current} is at v{version:03d}, but this hip has v{loaded:03d} loaded, "
        "so the job would not render what you see here. Press Reload on the "
        f"node that loads it to read v{version:03d}, or pin v{loaded:03d} there "
        "to keep it. Then Send again."
    )


def _write_denoise(folder: Path, product: Usd.Prim) -> commands.Denoise:
    """Write denoise.json and return what the denoise tasks need."""
    denoise.write_config(folder, denoise.config(denoise.var_names(product)))
    return commands.Denoise(product=product.GetName())


def _movie(node: hou.Node, configure: hou.Node) -> tuple[FFmpegPreset, Path, bool]:
    """Encode's preset, its movie, and whether Cleanup removes the encoded frames."""
    folder = Path(parms.text(configure, parms.OUTPUT))
    layer = parms.text(configure, parms.LAYER)
    preset = FFmpegPreset[parms.text(node, "preset")]
    video = parms.text(node, "output_file") or str(folder / f"{layer}.{preset.ext}")
    if not Path(video).is_absolute():
        raise SendRefused(
            f'Output File on {node.path()} is "{video}", which is not a full path. '
            "Start it with / or $HIP, or clear it."
        )
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
    return preset, Path(video), remove_frames


def _encode(
    node: hou.Node,
    configure: hou.Node,
    settings: UsdRender.Settings,
    products: list[Usd.Prim],
    denoised: Usd.Prim | None,
) -> commands.Encode:
    if denoised:
        # denoise_batch writes the finished beauty as R, G, B.
        product, channels = denoised, ["R,G,B"]
    else:
        product = paths.beauty_product(settings, products)
        name = paths.beauty(product)
        channels = [f"{name}.r,{name}.g,{name}.b", f"{name}.R,{name}.G,{name}.B"]
        channels.append("R,G,B")
    preset, video, remove_frames = _movie(node, configure)
    folder = Path(parms.text(configure, parms.OUTPUT))
    return commands.Encode(
        images=folder / product.GetName(),
        channels=channels,
        video=video,
        # A static method the stubs declare as an instance one.
        colorconfig=hou.Color.ocio_configPath(),  # ty: ignore[missing-argument]
        display=parms.text(node, "display"),
        view=parms.text(node, "view"),
        preset=preset,
        frame_rate=hou.fps(),
        remove_frames=remove_frames,
    )


def _setenv() -> str:
    # A variable the session lacks is left for the blade to choose.
    found = [f"{name}={value}" for name in ENV_VARS if (value := os.getenv(name))]
    return " ".join(["setenv", *found])


def _kind(node: hou.Node) -> str:
    return node.type().nameComponents()[2]
