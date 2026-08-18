from __future__ import annotations

from pxr import Sdf

# Prim paths inside a published rig.
RIG_ROOT_PATH = Sdf.Path("/rig")
RIG_GEO_PATH = RIG_ROOT_PATH.AppendChild("geo")

# Prim paths inside a shot's anim publish, which indexes rigs rather than holding
# one: a class prim per rig carrying that rig's animation, and a def per rig that
# references the rig asset and inherits the class. Both the chaser that writes the
# index and anything that reads it need these, so they live here.
ANIM_CLASS_PATH = Sdf.Path("/__class__/anim")
RIG_SCOPE_PATH = Sdf.Path("/rig")

# Prim path of a published shot camera. Solaris references the publish in under
# `/cameras` and scales it cm-to-m — see `SKD Import Camera`.
SHOT_CAM_PATH = Sdf.Path("/cameras/shotCam")
