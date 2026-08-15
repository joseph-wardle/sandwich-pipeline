import hou

# allow embedded variables to update
hou.allowEnvironmentToOverwriteVariable("HOUDINI_ASSETGALLERY_DB_FILE", True)
hou.allowEnvironmentToOverwriteVariable("JOB", True)


def _register_renamed_type_aliases() -> None:
    # Imported inside the function so that a failure reaching the pipeline
    # modules cannot stop the environment settings above from being applied.
    from pipe.dcc.houdini.util.nodetypes import RENAMED_FROM

    for new_name, old_name in RENAMED_FROM.items():
        node_type = hou.nodeType(hou.lopNodeTypeCategory(), new_name)
        if node_type is None:
            print(
                f"Houdini startup: {new_name} is not installed, so hips saved "
                f"before the rename will open with an unresolved {old_name} node."
            )
            continue
        if old_name in node_type.aliases():
            continue
        try:
            node_type.addAlias(old_name)
        except hou.Error as exc:
            print(
                f"Houdini startup: could not alias {old_name} to {new_name} "
                f"({exc}). A definition of {old_name} is probably still on "
                f"HOUDINI_PATH — remove it and relaunch."
            )


_register_renamed_type_aliases()
