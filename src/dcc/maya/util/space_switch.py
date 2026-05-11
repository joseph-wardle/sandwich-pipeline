from __future__ import annotations

import maya.cmds as mc

from dcc.maya.command import maya_command


def createSpaceSwitch():
    sel = mc.ls(selection=True)
    sources = sel

    target = sel[-1]
    sources.remove(target)
    sourceNames = []

    colonSourceStr = ""
    for source in sources:
        name = source.replace("_CTRL", "")
        if ":" in source:
            c = source.index(":")
            name = source[c + 1 :]
        colonSourceStr += name + ":"
        sourceNames.append(name)

    if mc.attributeQuery("spaceSwitch", node=target, exists=True):
        mc.deleteAttr(target, attribute="spaceSwitch")

    mc.addAttr(
        target,
        longName="spaceSwitch",
        attributeType="enum",
        enumName="default:" + colonSourceStr,
        keyable=True,
    )

    mc.select(target)
    grp = target + "_space_switch_GRP"

    parent = (mc.listRelatives(target, parent=True)[0],)

    if not mc.objExists(grp):
        grp = mc.group(
            name=target + "_space_switch_GRP",
            empty=True,
        )
        fix = mc.group(empty=True, name=target + "_space_switch_TARG")
        mc.matchTransform(fix, target)
        mc.parent(fix, grp)
        pc = mc.parentConstraint(fix, parent, maintainOffset=True)  # type: ignore

    if mc.listRelatives(grp, type="constraint") is not None:
        constraint = mc.listRelatives(grp, type="constraint")[0]
        mc.delete(constraint)
    pc = mc.parentConstraint(sources, grp, maintainOffset=True)[0]  # type: ignore

    pcTrgs = mc.parentConstraint(
        pc, weightAliasList=True, query=True, maintainOffset=True
    )

    defaultCond = mc.createNode("condition", name="default_COND")
    mc.connectAttr(target + ".spaceSwitch", defaultCond + ".firstTerm")
    mc.setAttr(defaultCond + ".colorIfTrueR", 0)  # type: ignore
    mc.setAttr(defaultCond + ".colorIfFalseR", 1)  # type: ignore
    mc.setAttr(defaultCond + ".operation", 0)  # type: ignore

    for count, source in enumerate(sources):
        print(source)
        print(pcTrgs[count])  # type: ignore
        cond = mc.createNode("condition", name=pcTrgs[count] + "_COND")  # type: ignore
        mc.connectAttr(target + ".spaceSwitch", cond + ".firstTerm")
        mc.setAttr(cond + ".secondTerm", count + 1)  # type: ignore
        mc.setAttr(cond + ".colorIfTrueR", 1)  # type: ignore
        mc.setAttr(cond + ".colorIfFalseR", 0)  # type: ignore
        mc.connectAttr(defaultCond + ".outColorR", cond + ".colorIfTrueR")
        mc.connectAttr(cond + ".outColorR", pc + "." + pcTrgs[count])  # type: ignore

    mc.select(target)


@maya_command(
    name="space_switch", label="Space Switch", category="animation", icon="parent.png"
)
def run():
    """
    Creates a animation space switch setup. All the selected objects become switchable spaces for the final object in the selection.

    For example:
        Select the COG, chest, and hip controls, then finally the IK hand control, then run this command.
        The IK hand will now have an attribute that lets you switch between COG, chest, and hip space.
    """
    createSpaceSwitch()
