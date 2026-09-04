"""Startup registration for the loose vendored gizmos in `third_party`.

Nuke runs this automatically because site/init.py puts this folder on the
plugin path. Plugin paths belong here rather than in menu.py so the gizmos
also resolve in headless Nuke (`nuke -t`, farm renders), where menu.py never
runs and a comp containing the node would otherwise fail to load.

Toolkits that ship their own init.py/menu.py (NukeSurvivalToolkit) are added
directly in site/init.py instead.
"""

import os

import nuke

THIRD_PARTY = os.environ["DCC_NUKE_THIRD_PARTY"]

# Gizmos that ship on their own, without a toolkit folder, sit at the root of
# `third_party` (df_kuwahara_map.gizmo). Adding the root registers each one as
# a node class named after its filename minus the extension.
nuke.pluginAddPath(THIRD_PARTY)
