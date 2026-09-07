"""Register non-plugin third party gizmos."""

import os

import nuke

THIRD_PARTY = os.environ["DCC_NUKE_THIRD_PARTY"]

nuke.pluginAddPath(THIRD_PARTY)
