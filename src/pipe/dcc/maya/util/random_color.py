"""Blender-style per-material ID colors for the modeling viewport: a reversible toggle that answers "which faces share a material?" at a glance."""

from __future__ import annotations

import logging

import maya.cmds as mc

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

# sRGB hex. The first seven are the Okabe-Ito colorblind-safe set. The last five
# were picked by maximizing the minimum Oklab distance -- under normal,
# deuteranope and protanope vision, and after the show's ACEScg -> OpenDRT view
# transform -- against each other and against the grey of Maya's default shader,
# which unassigned geometry keeps. They cost nothing: the closest pair across all
# twelve is one of the Okabe-Ito seven, which bound the set either way. Scenes
# with more materials than this cycle, because twelve is already past the point
# where a human separates flat colors.
PALETTE_SRGB: tuple[str, ...] = (
    "E69F00",  # orange
    "56B4E9",  # sky blue
    "009E73",  # bluish green
    "F0E442",  # yellow
    "0072B2",  # blue
    "D55E00",  # vermillion
    "CC79A7",  # reddish purple
    "A00060",  # magenta
    "2000E0",  # violet
    "2080FF",  # azure
    "C02000",  # red
    "80FFFF",  # pale cyan
)

# Rec.709/D65 -> AP1/D60 with Bradford adaptation. Verified against the show
# config's own "sRGB - Texture" -> "ACEScg" processor to within 2e-5.
_SRGB_TO_ACESCG = (
    (0.6130974, 0.3395231, 0.0473795),
    (0.0701942, 0.9163538, 0.0134520),
    (0.0206156, 0.1095698, 0.8698146),
)


def _acescg_from_srgb_hex(value: str) -> tuple[float, float, float]:
    """Maya stores shader colors in the rendering space, which this show sets to ACEScg -- writing sRGB numbers raw would oversaturate them and collapse the palette under OpenDRT."""
    encoded = [int(value[index : index + 2], 16) / 255.0 for index in (0, 2, 4)]
    linear = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in encoded
    ]
    return (
        sum(_SRGB_TO_ACESCG[0][i] * linear[i] for i in range(3)),
        sum(_SRGB_TO_ACESCG[1][i] * linear[i] for i in range(3)),
        sum(_SRGB_TO_ACESCG[2][i] * linear[i] for i in range(3)),
    )


def _paintable_shading_engines() -> tuple[list[str], int]:
    """Scene shadingEngines this tool may rebind, plus how many were skipped for coming from a reference."""
    paintable: list[str] = []
    skipped = 0
    for shading_engine in mc.ls(type="shadingEngine") or []:
        if shading_engine in RESERVED_SHADING_ENGINES:
            continue
        # Rebinding a referenced node writes a reference edit that outlives the
        # toggle, so referenced materials keep their own look and are reported.
        if mc.referenceQuery(shading_engine, isNodeReferenced=True):
            skipped += 1
            continue
        paintable.append(shading_engine)
    return sorted(paintable), skipped


def _stamped_shading_engines() -> list[str]:
    return sorted(
        shading_engine
        for shading_engine in mc.ls(type="shadingEngine") or []
        if mc.attributeQuery(SOURCE_PLUG_ATTR, node=shading_engine, exists=True)
    )


def is_random_color_active() -> bool:
    """True while the scene is showing ID colors instead of its own shaders. Read by the asset publisher, which refuses to publish a stamped scene."""
    return bool(_stamped_shading_engines())


def _delete_stamp_shaders() -> None:
    strays = [
        node
        for node in mc.ls(type="lambert") or []
        if node.rpartition(":")[2].startswith(STAMP_SHADER_PREFIX)
    ]
    if strays:
        mc.delete(strays)  # type: ignore


def _stamp() -> tuple[int, int]:
    """Point every paintable shadingEngine at a throwaway flat lambert, recording what it pointed at before. Mutates the scene. Returns the colored count and the referenced count left alone."""
    shading_engines, skipped = _paintable_shading_engines()
    stamped = 0
    for shading_engine in shading_engines:
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

        color = _acescg_from_srgb_hex(PALETTE_SRGB[stamped % len(PALETTE_SRGB)])
        shader: str = mc.shadingNode(
            "lambert", asShader=True, name=f"{STAMP_SHADER_PREFIX}{stamped:02d}"
        )
        mc.setAttr(f"{shader}.color", *color, type="double3")  # type: ignore
        # Lambert dims diffuse to 0.8 by default; the viewport should show the
        # palette color, not four fifths of it.
        mc.setAttr(f"{shader}.diffuse", 1.0)  # type: ignore

        mc.addAttr(shading_engine, longName=SOURCE_PLUG_ATTR, dataType="string")
        mc.setAttr(f"{shading_engine}.{SOURCE_PLUG_ATTR}", source[0], type="string")
        mc.connectAttr(
            f"{shader}.outColor", f"{shading_engine}.surfaceShader", force=True
        )
        stamped += 1
    return stamped, skipped


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
    _delete_stamp_shaders()
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
    """
    Toggle a flat ID color on every material in the scene so you can see at a glance which
    faces share a material. Nothing but the shadingEngine's surfaceShader connection is
    touched, and switching it off reconnects the originals. Publishing is blocked while
    it is on.

    "Random" is Blender's name for the same viewport mode and is kept for recognition. The
    colors are not random: shadingEngines are sorted by name and take a fixed palette in
    order, so the same scene always looks the same.
    """
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
            f"Random Colors did nothing: this scene's only {skipped} material(s) come "
            "from a reference, which this tool leaves alone."
            if skipped
            else "Random Colors did nothing: this scene has no materials of its own to color."
        )
        return

    message = f"Random Colors on. {stamped} material(s) colored"
    if skipped:
        message += f", {skipped} skipped (referenced)"
    _flash(message + ". Turn it off before publishing.")
