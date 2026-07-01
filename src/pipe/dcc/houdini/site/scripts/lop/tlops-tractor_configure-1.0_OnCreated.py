import hou

try:
    me: hou.Node = kwargs["node"]  # type: ignore[name-defined] # noqa: F821
    overscan = me.parm("overscan")
    renderer = me.parm("renderer")
    assert overscan is not None
    assert renderer is not None
    overscan.set(6.0)
    renderer.set("HdPrmanLoaderRendererPlugin")
except Exception:  # in case this is created as a locked node
    pass
