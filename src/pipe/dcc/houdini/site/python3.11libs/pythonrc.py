import hou

# allow embedded variables to update
hou.allowEnvironmentToOverwriteVariable("HOUDINI_ASSETGALLERY_DB_FILE", True)
hou.allowEnvironmentToOverwriteVariable("JOB", True)


def _register_renamed_type_aliases() -> None:
    # Imported inside the function so that a failure reaching the pipeline
    # modules cannot stop the environment settings above from being applied.
    from pipe.dcc.houdini.util.nodetypes import RENAMED_FROM, RETIRED_SOPS

    hou.hda.reloadAllFiles()

    for new_name, old_name in RENAMED_FROM.items():
        _alias(hou.lopNodeTypeCategory(), new_name, old_name)
    for old_name in RETIRED_SOPS:
        _alias(hou.sopNodeTypeCategory(), "null", old_name)


def _alias(category: hou.NodeTypeCategory, new_name: str, old_name: str) -> None:
    node_type = hou.nodeType(category, new_name)
    if node_type is None:
        print(
            f"Houdini startup: {new_name} is not installed, so hips saved "
            f"before the rename will open with an unresolved {old_name} node."
        )
        return
    if old_name in node_type.aliases():
        return
    try:
        node_type.addAlias(old_name)
    except hou.Error as exc:
        print(
            f"Houdini startup: could not alias {old_name} to {new_name} "
            f"({exc}). A definition of {old_name} is probably still on "
            f"HOUDINI_PATH — remove it and relaunch."
        )


_register_renamed_type_aliases()
