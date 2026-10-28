"""The buttons the site adds to every stock File Cache, and keeps out of saved hips."""

from __future__ import annotations

import hou

from pipe.dcc.houdini.util.nodetypes import FILE_CACHES

BUTTON = "skd_cache_send"
WEDGE_BUTTON = "skd_wedge_send"
REVIEW_BUTTON = "skd_wedge_review"
# Name, label, callback and help of each button, in the order they sit in one row.
BUTTONS = (
    (
        BUTTON,
        "Save to Disk on Tractor",
        "from pipe.dcc.houdini.tractor import cache_send; "
        "cache_send.send(kwargs['node'])",
        "Write this cache on the farm, from a copy of the scene as it is now.",
    ),
    (
        WEDGE_BUTTON,
        "Wedge on Tractor",
        "from pipe.dcc.houdini.tractor import wedge_send; "
        "wedge_send.send(kwargs['node'])",
        "Sim this cache on the farm once for each wedged value, and flipbook each.",
    ),
    (
        REVIEW_BUTTON,
        "Review Wedges",
        "from pipe.dcc.houdini.tractor import wedge_review; "
        "wedge_review.review(kwargs['node'])",
        "Play the finished wedges of this cache, and apply one's values.",
    ),
)
# Save to Disk's row ends with Cancel Cook, so the buttons start the row below.
BUTTON_AFTER = "dirtyall"
# Any name, so folders compare by everything but their name.
FOLDER = "folder"


def add_buttons(node: hou.Node) -> None:
    """Run by the site's event scripts when a File Cache is created or loaded,
    and after a save."""
    if node.isInsideLockedHDA():
        return
    group = node.parmTemplateGroup()
    after = BUTTON_AFTER
    changed = False
    for index, (name, label, callback, help_text) in enumerate(BUTTONS):
        # A copied node brings its buttons along, and adding one twice raises.
        if group.find(name) is None:
            button = hou.ButtonParmTemplate(
                name,
                label,
                script_callback=callback,
                script_callback_language=hou.scriptLanguage.Python,
                help=help_text,
                join_with_next=index < len(BUTTONS) - 1,
            )
            group.insertAfter(after, button)
            changed = True
        after = name
    if changed:
        node.setParmTemplateGroup(group)


def strip_buttons(node: hou.Node) -> None:
    """Run before a save. Any spare parm makes the hip save the node's whole
    layout as spares, so the buttons stay out of the file and come back after."""
    if node.isInsideLockedHDA():
        return
    for name, *_ in BUTTONS:
        if parm := node.parmTuple(name):
            # The stubs, made for Houdini 19.5, leave this method out.
            node.removeSpareParmTuple(parm)  # ty: ignore[unresolved-attribute]
    # Removing the buttons leaves the layout's folders behind as spares, which
    # warn on the next load. An artist's own edit to the layout, even one with
    # no spare parm such as a hidden parm, is saved by those spares.
    if _layout(node.parmTemplateGroup().entries()) == _layout(
        node.type().parmTemplateGroup().entries()
    ):
        node.removeSpareParms()


def _layout(
    templates: tuple[hou.ParmTemplate, ...], multiparm: bool = False
) -> list[hou.ParmTemplate]:
    """The templates with folder names left out, since Houdini renames the
    folders each time a layout is set. Setting one also drops the joins of a
    multiparm's parms, such as the LOP File Cache's options, so those joins
    are left out too."""
    found = []
    for template in templates:
        if isinstance(template, hou.FolderParmTemplate):
            folder = template.clone()
            folder.setName(FOLDER)
            # The stubs type clone() as returning a plain ParmTemplate.
            folder.setParmTemplates(())  # ty: ignore[unresolved-attribute]
            found.append(folder)
            inner = template.folderType() == hou.folderType.MultiparmBlock
            found.extend(_layout(template.parmTemplates(), multiparm or inner))
        elif multiparm:
            unjoined = template.clone()
            unjoined.setJoinWithNext(False)
            found.append(unjoined)
        else:
            found.append(template)
    return found


def on_save(event: hou.hipFileEventType) -> None:
    """The site's hip file event callback."""
    if event == hou.hipFileEventType.BeforeSave:
        change = strip_buttons
    elif event == hou.hipFileEventType.AfterSave:
        change = add_buttons
    else:
        return
    # Neither is the artist's edit, so neither goes on their undo stack.
    with hou.undos.disabler():
        for name in FILE_CACHES:
            # nodeType is None for a type not installed; both ship with Houdini.
            node_type = hou.nodeType(name)
            for node in node_type.instances():  # ty: ignore[unresolved-attribute]
                change(node)
