"""Flat per-material ID colors for the modeling viewport: a reversible toggle that answers "which faces share a material?" at a glance."""

from __future__ import annotations

import logging
import math

import maya.cmds as mc
import PyOpenColorIO as ocio

from pipe.dcc.maya.command import maya_command, undo_chunk

# maya-stubs types every cmds variadic as `*args: str`, so the ignore comments
# below sit on calls that legitimately pass floats or a list of node names.

log = logging.getLogger(__name__)

# Maya renders an inViewMessage as HTML, so a colored span tints the feedback by severity.
_WARN_COLOR = "#f4b400"

# Backup and on/off state in one attribute: the plug that used to drive
# `<shadingEngine>.surfaceShader`. The mode is on exactly when some
# shadingEngine carries it, so there is no separate flag to fall out of sync.
SOURCE_PLUG_ATTR = "skdRandomColorSource"
STAMP_SHADER_PREFIX = "SKD_randomColor_"

# `initialShadingGroup` is deliberately left alone so unassigned geometry keeps
# Maya's default grey and stays readable as "no material" against every ID color.
RESERVED_SHADING_ENGINES = frozenset({"initialShadingGroup", "initialParticleSE"})


# --- palette ---------------------------------------------------------------

GRID_STEPS = 16
GRID_TOP = 2.0

# Widening this scores better by the metric below and looks worse: a near-black
# material reads as shadow, a blown-out one as a specular hit.
MIN_LIGHTNESS, MAX_LIGHTNESS = 0.40, 0.82

# Separation every color keeps under simulated dichromacy. Costs nothing in
# normal vision and roughly triples the worst-case gap for a colorblind artist.
CVD_FLOOR = 0.030

# What unassigned geometry looks like, and so what every ID color is kept away
# from: `initialShadingGroup` is driven by `openPBR_shader1`, whose baseColor is
# this grey at full baseWeight.
UNASSIGNED_GREY = (0.4, 0.4, 0.4)

# Deuteranopia and protanopia at full severity (Machado, Oliveira & Fernandes 2009).
DICHROMAT_MATRICES = (
    (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
)

_Lab = tuple[float, float, float]


def _transform(matrix: tuple[tuple[float, ...], ...], rgb: list[float]) -> list[float]:
    return [sum(row[i] * rgb[i] for i in range(3)) for row in matrix]


def _oklab(linear_rgb: list[float]) -> _Lab:
    """Oklab (Ottosson 2020): euclidean distance in it tracks how different two colors look, which distance in RGB does not."""
    r, g, b = (max(0.0, channel) for channel in linear_rgb)
    long = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    medium = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    short = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * long + 0.7936177850 * medium - 0.0040720468 * short,
        1.9779984951 * long - 2.4285922050 * medium + 0.4505937099 * short,
        0.0259040371 * long + 0.7827717662 * medium - 0.8086757660 * short,
    )


def _viewport_view() -> ocio.CPUProcessor | None:
    """The color transform the viewport applies before you see anything, or None when Maya is not color-managing."""
    try:
        if not mc.colorManagementPrefs(query=True, cmEnabled=True):
            return None
        # GetCurrentConfig is the config Maya itself loaded, so this follows the
        # artist's active view rather than assuming the show's.
        transform = ocio.DisplayViewTransform(
            src=str(mc.colorManagementPrefs(query=True, renderingSpaceName=True)),
            display=str(mc.colorManagementPrefs(query=True, displayName=True)),
            view=str(mc.colorManagementPrefs(query=True, viewName=True)),
        )
        return ocio.GetCurrentConfig().getProcessor(transform).getDefaultCPUProcessor()
    except Exception:
        log.exception(
            "Random Colors could not read the viewport color transform; picking "
            "colors from their stored values instead, which separates them less well."
        )
        return None


def _appearance(
    color: tuple[float, float, float], view: ocio.CPUProcessor | None
) -> tuple[_Lab, tuple[_Lab, ...]]:
    """How a rendering-space color lands on screen, in Oklab: for normal vision, then for each dichromat."""
    displayed = view.applyRGB(list(color)) if view is not None else list(color)
    linear = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in (min(1.0, max(0.0, channel)) for channel in displayed)
    ]
    return (
        _oklab(linear),
        tuple(_oklab(_transform(matrix, linear)) for matrix in DICHROMAT_MATRICES),
    )


def _palette(count: int) -> list[tuple[float, float, float]]:
    """`count` rendering-space colors, each as far as the gamut allows from all the others and from unassigned-geometry grey.

    Greedy farthest-point selection (Glasbey et al. 2007, "Colour Displays for
    Categorical Images").
    """
    if count <= 0:
        return []
    view = _viewport_view()
    axis = [((step / (GRID_STEPS - 1)) ** 2.2) * GRID_TOP for step in range(GRID_STEPS)]
    candidates = []
    for red in axis:
        for green in axis:
            for blue in axis:
                normal, dichromat = _appearance((red, green, blue), view)
                if MIN_LIGHTNESS <= normal[0] <= MAX_LIGHTNESS:
                    candidates.append(((red, green, blue), normal, dichromat))

    def dichromat_gap(one: tuple[_Lab, ...], other: tuple[_Lab, ...]) -> float:
        return min(math.dist(a, b) for a, b in zip(one, other))

    grey, grey_dichromat = _appearance(UNASSIGNED_GREY, view)
    gaps = [math.dist(normal, grey) for _, normal, _ in candidates]
    cvd_gaps = [dichromat_gap(d, grey_dichromat) for _, _, d in candidates]

    chosen: list[tuple[float, float, float]] = []
    for _ in range(count):
        # Past the point where the gamut runs out of dichromat-safe colors, fall
        # back to the whole pool rather than returning fewer colors than asked.
        safe = [index for index, gap in enumerate(cvd_gaps) if gap >= CVD_FLOOR]
        pick = max(safe or range(len(candidates)), key=lambda index: gaps[index])
        color, normal, dichromat = candidates[pick]
        chosen.append(color)
        for index, (_, other, other_dichromat) in enumerate(candidates):
            gaps[index] = min(gaps[index], math.dist(normal, other))
            cvd_gaps[index] = min(
                cvd_gaps[index], dichromat_gap(dichromat, other_dichromat)
            )
    return chosen


# --- scene -----------------------------------------------------------------


def _is_referenced(node: str) -> bool:
    return bool(mc.referenceQuery(node, isNodeReferenced=True))


def _paintable() -> tuple[list[tuple[str, str]], int]:
    """Every shadingEngine this tool may rebind paired with the plug currently
    driving it, plus how many a reference owns and we left alone."""
    paintable: list[tuple[str, str]] = []
    skipped = 0
    for shading_engine in sorted(mc.ls(type="shadingEngine") or []):
        if shading_engine in RESERVED_SHADING_ENGINES:
            continue
        if _is_referenced(shading_engine):
            skipped += 1
            continue
        source: list[str] = (
            mc.listConnections(
                f"{shading_engine}.surfaceShader",
                plugs=True,
                source=True,
                destination=False,
            )
            or []
        )
        if not source:
            continue  # nothing drives it, so there is nothing to hide or restore
        if _is_referenced(source[0].partition(".")[0]):
            skipped += 1
            continue
        paintable.append((shading_engine, source[0]))
    return paintable, skipped


def _stamped_shading_engines() -> list[str]:
    """Local shadingEngines currently showing an ID color. A referenced one carries
    its own file's marker and is none of this scene's business."""
    return sorted(
        shading_engine
        for shading_engine in mc.ls(type="shadingEngine") or []
        if mc.attributeQuery(SOURCE_PLUG_ATTR, node=shading_engine, exists=True)
        and not _is_referenced(shading_engine)
    )


def is_random_color_active() -> bool:
    """True while this scene is showing ID colors instead of its own shaders. Read
    by the asset publisher, which refuses to publish a stamped scene."""
    return bool(_stamped_shading_engines())


def _stamp() -> tuple[int, int]:
    """Point every paintable shadingEngine at a throwaway flat lambert, recording what
    it pointed at before. Mutates the scene. Returns the colored count and the referenced count left alone."""
    paintable, skipped = _paintable()
    palette = _palette(len(paintable))
    for index, (shading_engine, source) in enumerate(paintable):
        shader: str = mc.shadingNode(
            "lambert", asShader=True, name=f"{STAMP_SHADER_PREFIX}{index:02d}"
        )
        mc.setAttr(f"{shader}.color", *palette[index], type="double3")  # type: ignore
        # Lambert dims diffuse to 0.8 by default; the viewport should show the
        # palette color, not four fifths of it.
        mc.setAttr(f"{shader}.diffuse", 1.0)  # type: ignore

        mc.addAttr(shading_engine, longName=SOURCE_PLUG_ATTR, dataType="string")
        mc.setAttr(f"{shading_engine}.{SOURCE_PLUG_ATTR}", source, type="string")
        mc.connectAttr(
            f"{shader}.outColor", f"{shading_engine}.surfaceShader", force=True
        )
    return len(paintable), skipped


def _restore() -> tuple[int, list[str]]:
    """Reconnect every stamped shadingEngine to the plug it recorded and delete the throwaway shaders. Mutates the scene. Returns the restored count and any shadingEngine whose original shader no longer exists."""
    restored = 0
    orphaned: list[str] = []
    for shading_engine in _stamped_shading_engines():
        marker = f"{shading_engine}.{SOURCE_PLUG_ATTR}"
        source: str = mc.getAttr(marker)
        mc.deleteAttr(marker)
        if source and mc.objExists(source):
            mc.connectAttr(source, f"{shading_engine}.surfaceShader", force=True)
            restored += 1
        else:
            orphaned.append(shading_engine)

    strays = [
        node
        for node in mc.ls(type="lambert") or []
        if node.rpartition(":")[2].startswith(STAMP_SHADER_PREFIX)
        and not _is_referenced(node)
    ]
    if strays:
        mc.delete(strays)  # type: ignore
    return restored, orphaned


def _flash(message: str) -> None:
    mc.inViewMessage(message=message, position="midCenter", fade=True)


def _warn(message: str) -> None:
    _flash(f'<span style="color:{_WARN_COLOR}">{message}</span>')


@maya_command(
    name="random_colors",
    label="Random Colors",
    category="modeling",
    icon="random-colors.svg",
)
def random_colors() -> None:
    """Toggle a flat ID color on every material in the scene so you can see at a glance which
    faces share a material."""
    if is_random_color_active():
        with undo_chunk("Random Colors Off"):
            restored, orphaned = _restore()
        if orphaned:
            _warn(
                f"Random Colors off. Restored {restored} material(s), but "
                f"{len(orphaned)} lost the shader they were bound to and now have "
                f"none: {', '.join(orphaned)}."
            )
        else:
            _flash(f"Random Colors off. Restored {restored} material(s).")
        return

    with undo_chunk("Random Colors On"):
        try:
            stamped, skipped = _stamp()
        except Exception:
            log.exception(
                "Random Colors failed while rebinding shadingEngines; rolling back."
            )
            _restore()
            _warn(
                "Could not apply Random Colors, so the scene was left as it was. "
                "See the Script Editor for details."
            )
            return

    if not stamped:
        _warn(
            f"Random Colors did nothing: this scene's only {skipped} material(s) are "
            "owned by a reference, which this tool leaves alone."
            if skipped
            else "Random Colors did nothing: this scene has no materials of its own to color."
        )
        return

    message = f"Random Colors on. {stamped} material(s) colored"
    if skipped:
        message += f", {skipped} skipped (referenced)"
    _flash(message + ". Turn it off before publishing.")
