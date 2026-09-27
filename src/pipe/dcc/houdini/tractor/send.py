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
from pxr import Usd, UsdRender

from pipe.dcc.houdini.tractor import SendRefused, denoise, folders, job, paths

CONFIGURE = "tractor_configure"
DENOISE = "tractor_denoise"
ENCODE = "tractor_encode_video"

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
    url = _text(submit, "engine_url")
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
            f'Look for "{title}" at {url} before sending again.'
            "\n\nIts folders were kept:\n" + "\n".join(str(f) for f in claimed),
            title="Tractor did not confirm the job",
            severity=hou.severityType.Warning,
            details=traceback.format_exc(),
        )
    else:
        hou.ui.displayMessage(
            f"Job {job_id} renders into:\n"
            + "\n".join(str(folder) for folder in claimed)
            + f"\n\nVisit {url} to check progress.",
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
    author.setEngineClientParam(
        hostname=_text(submit, "engine_url"), port=int(submit.evalParm("engine_port"))
    )
    return job.build(
        _text(submit, "title"),
        int(submit.evalParm("priority")),
        _setenv(submit),
        [_layer(c) for c in chains],
    )


def _layer(chain: Chain) -> job.Layer:
    configure = chain.configure
    folder = Path(_text(configure, folders.OUTPUT))
    _write_render_usd(configure, chain.output)

    stage = Usd.Stage.Open(str(folder / paths.RENDER_USD), Usd.Stage.LoadNone)
    settings = paths.rendered_settings(stage, _toggled(configure, "rendersettings"))
    products = paths.products(settings)
    denoised = denoise.product(products) if chain.denoise else None
    frames = _frames(configure)
    outputs = products + paths.cryptomattes(settings)
    written = paths.author_outputs(
        stage, frames, outputs, [denoised] if denoised else []
    )

    tiles = configure.evalParmTuple("husk_tilecount")
    render = job.Render(
        husk=_husk(configure),
        folders=written,
        tiles=int(tiles[0]) * int(tiles[1]) if configure.evalParm("husk_tile") else 0,
        timelimit=int(_toggled(configure, "husk_timelimit") or 0),
        service=_text(configure, "service"),
    )
    denoise_spec = None
    if chain.denoise and denoised:
        denoise_spec = _write_denoise(chain.denoise, folder, denoised, frames)
    encode_spec = None
    if chain.encode:
        encode_spec = _encode(chain.encode, folder, settings, products, denoised)
    name = _text(configure, folders.LAYER)
    return job.Layer(name, folder, frames, render, denoise_spec, encode_spec)


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
        raise hou.OperationFailed("\n".join(errors))


def _frames(configure: hou.LopNode) -> list[int]:
    trange = _text(configure, "trange")
    if trange == "off":
        return [int(hou.frame())]
    if trange == "stage":
        stage = configure.stage()
        start = int(stage.GetStartTimeCode() - float(configure.evalParm("foffset1")))
        end = int(stage.GetEndTimeCode() + float(configure.evalParm("foffset2")))
        return list(range(start, end + 1, int(configure.evalParm("foffset3"))))
    start, end, step = (int(v) for v in configure.evalParmTuple("f"))
    return list(range(start, end + 1, step))


def _husk(configure: hou.Node) -> list[str]:
    words = [
        *("--renderer", _text(configure, "renderer")),
        *("--purpose", _text(configure, "husk_purpose")),
        *("--complexity", "veryhigh"),
        *("--verbose", f"acet{_text(configure, 'verbosity')}"),
    ]
    if int(_toggled(configure, "snapshot") or 0):
        words += ["--snapshot", _text(configure, "snapshot")]
    if camera := _toggled(configure, "override_camera"):
        words += ["--camera", camera]
    if settings := _toggled(configure, "rendersettings"):
        words += ["--settings", settings]
    if configure.evalParm("husk_instantshutter"):
        words.append("--disable-motionblur")
    if configure.evalParm("husk_tile"):
        count = configure.evalParmTuple("husk_tilecount")
        words += ["--tile-count", str(count[0]), str(count[1])]
        words += ["--tile-suffix", _text(configure, "husk_tilesuffix")]
    if trace := _text(configure, "husk_usdtrace"):
        words += ["--usd-trace", trace]
        if chrome := _text(configure, "husk_chromefile"):
            words += ["--usd-chrome-file", chrome]
    words.append("--disable-dummy-raster-product")
    return words


def _write_denoise(
    node: hou.Node, folder: Path, product: Usd.Prim, frames: list[int]
) -> job.Denoise:
    """Write denoise.json and return what the denoise tasks need."""
    topology = denoise.Topology[_text(node, "topology")]
    if topology in denoise.MULTIFRAME and frames != list(
        range(frames[0], frames[-1] + 1)
    ):
        raise SendRefused(
            "Multiframe denoising reads each frame's neighbours, so render every "
            "frame of the range, or choose a single-frame topology."
        )
    config = denoise.config(
        denoise.var_names(product),
        topology,
        float(node.evalParm("asymmetry")),
        (int(node.evalParm("tilesx")), int(node.evalParm("tilesy"))),
    )
    denoise.write_config(folder, config)
    return job.Denoise(
        product=product.GetName(),
        topology=topology,
        service=_text(node, "service"),
    )


def _encode(
    node: hou.Node,
    folder: Path,
    settings: UsdRender.Settings,
    products: list[Usd.Prim],
    denoised: Usd.Prim | None,
) -> job.Encode:
    if denoised:
        # denoise_batch writes the finished beauty as R, G, B.
        product, channels = denoised, ["R,G,B"]
    else:
        product = _beauty_product(settings, products)
        name = paths.beauty(product)
        # RenderMan names the beauty after its var, or R, G, B when it is
        # first; a var named in the frame is the surer match.
        channels = [f"{name}.r,{name}.g,{name}.b", f"{name}.R,{name}.G,{name}.B"]
        channels.append("R,G,B")
    codec = _text(node, "codec")
    video = _text(node, "output_file") or str(
        folder / ("video.mov" if codec == "prores" else "video.mp4")
    )
    return job.Encode(
        images=folder / product.GetName(),
        channels=channels,
        video=Path(video),
        framerate=int(node.evalParm("framerate")),
        # A static method the stubs declare as an instance one.
        colorconfig=hou.Color.ocio_configPath(),  # ty: ignore[missing-argument]
        display=_text(node, "display"),
        view=_text(node, "view"),
        codec=codec,
        quality=int(node.evalParm("quality")),
        service=_text(node, "service"),
    )


def _beauty_product(settings: UsdRender.Settings, products: list[Usd.Prim]) -> Usd.Prim:
    # RenderMan fills R, G, B with whichever var comes first, so a product
    # without a beauty var would make a movie of another pass.
    found = [p for p in products if paths.beauty(p)]
    if len(found) != 1:
        names = ", ".join(str(p.GetPath()) for p in found) or "none"
        raise SendRefused(
            f"Encode needs exactly one render product of {settings.GetPath()} with "
            f"a beauty (Ci) render var, but found: {names}. Add the beauty to one "
            "product, or remove Encode."
        )
    return found[0]


def _setenv(submit: hou.Node) -> str:
    names = _text(submit, "env_vars").split()
    return " ".join(
        ["setenv"]
        + [f"{name}={os.getenv(name)}" for name in names]
        + [f"HOUDINI_LICENSE_SERVER={_text(submit, 'license_server')}"]
    )


def _kind(node: hou.Node) -> str:
    return node.type().nameComponents()[2]


def _text(node: hou.Node, name: str) -> str:
    return str(node.evalParm(name))


def _toggled(node: hou.Node, name: str) -> str:
    """A parm's value when its checkbox is on, else empty."""
    return _text(node, name) if node.evalParm(f"toggle_{name}") else ""
