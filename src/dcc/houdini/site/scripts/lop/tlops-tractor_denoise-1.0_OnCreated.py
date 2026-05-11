import hou
from core.util.util import get_production_path


try:
    me: hou.Node = kwargs["node"]  # type: ignore[name-defined] # noqa: F821
    rmantree = me.parm("rmantree_override")
    assert rmantree is not None
    rmantree.set(str(get_production_path() / "opt/pixar/RenderManProServer-26.3"))
except Exception:  # in case this is created as a locked node
    pass
