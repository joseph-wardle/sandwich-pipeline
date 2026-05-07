from ...test.core import RigBuildTest
from .control import TestControlsInSet, TestControlsTagged, TestControlsZeroed
from .cycle import TestCyclesDG
from .duplicate import TestDuplicateDagNames
from .geo import TestGeoInGroup, TestGeoInSet, TestGeoNotSelectable
from .hierarchy import TestRootNodeNaming, TestSingleHierachy
from .joint import TestHiddenJoints
from .namespace import TestNamespaces
from .ng import TestNgSkinData
from .node import TestUnknownNodes
from .root_transform import TestRootRotate, TestRootScale, TestRootTranslation
from .visibility import TestHiddenRigNodes

RIG_BUILD_TESTS: list[type[RigBuildTest]] = [
    TestHiddenJoints,
    TestControlsInSet,
    TestControlsTagged,
    TestControlsZeroed,
    TestDuplicateDagNames,
    TestGeoInSet,
    TestGeoInGroup,
    TestGeoNotSelectable,
    TestSingleHierachy,
    TestRootNodeNaming,
    TestNamespaces,
    TestHiddenRigNodes,
    TestUnknownNodes,
    TestCyclesDG,
    TestNgSkinData,
    TestRootTranslation,
    TestRootRotate,
    TestRootScale,
]

__all__ = [
    "RIG_BUILD_TESTS",
    "TestControlsInSet",
    "TestControlsTagged",
    "TestControlsZeroed",
    "TestCyclesDG",
    "TestDuplicateDagNames",
    "TestGeoInGroup",
    "TestGeoInSet",
    "TestGeoNotSelectable",
    "TestSingleHierachy",
    "TestRootNodeNaming",
    "TestHiddenJoints",
    "TestNamespaces",
    "TestNgSkinData",
    "TestUnknownNodes",
    "TestRootTranslation",
    "TestRootRotate",
    "TestRootScale",
    "TestHiddenRigNodes",
]
