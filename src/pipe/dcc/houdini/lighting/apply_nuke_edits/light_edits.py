import json
import os

import hou


LIGHTS_PREFIX = "/lights/"
DEFAULT_JSON_RELATIVE = os.path.join("..", "comp", "light_edits.json")
COLOR_PARM_CANDIDATES = ("lightcolor", "color", "lightColor", "xn__inputscolor_zta")
INTENSITY_PARM_CANDIDATES = ("intensity", "lightIntensity", "xn__inputsintensity_i0a")


def _rebalance(old_color, old_intensity, multiply):
    """Apply a per-channel multiply to (color, intensity) and re-normalize so
    the resulting color's max channel is 1.0. Total light contribution per
    channel is preserved: new_color * new_intensity == old_color * multiply *
    old_intensity. If the product is all zero, intensity drops to 0."""
    combined = tuple(old_color[i] * multiply[i] for i in range(3))
    m = max(combined)
    if m <= 0:
        return combined, 0.0
    return tuple(c / m for c in combined), old_intensity * m


def _resolve_default_json_path():
    hip = hou.expandString("$HIP")
    return os.path.normpath(os.path.join(hip, DEFAULT_JSON_RELATIVE))


def _load_edits(json_path):
    if not os.path.exists(json_path):
        raise RuntimeError("Light edits JSON not found: %s" % json_path)
    with open(json_path) as f:
        return json.load(f)


def _find_color_parm(node):
    for name in COLOR_PARM_CANDIDATES:
        pt = node.parmTuple(name)
        if pt is not None and len(pt) >= 3:
            return pt
    for pt in node.parmTuples():
        n = pt.name()
        if n.startswith("xn__inputscolor_") and len(pt) >= 3:
            return pt
    return None


def _find_intensity_parm(node):
    for name in INTENSITY_PARM_CANDIDATES:
        p = node.parm(name)
        if p is not None:
            return p
    for p in node.parms():
        n = p.name()
        if n.startswith("xn__inputsintensity_"):
            return p
    return None


def apply_to_stage(stage, json_path):
    """For each branch under /lights whose name matches a key in the JSON,
    multiply every light prim's color component-wise by the branch's
    multiply RGB, then re-normalize the color so its max channel is 1.0 and
    absorb the brightness factor into intensity. Preserves total per-channel
    contribution (new_color * new_intensity == old_color * multiply *
    old_intensity). Use from a Python LOP."""
    from pxr import Usd, UsdLux

    edits = _load_edits(json_path)
    lights_prim = stage.GetPrimAtPath("/lights")
    if not lights_prim:
        return 0

    matched = 0
    for branch in lights_prim.GetChildren():
        entry = edits.get(branch.GetName())
        if not entry:
            continue
        multiply = entry.get("multiply")
        if not multiply or len(multiply) < 3:
            continue
        for prim in Usd.PrimRange(branch):
            if not prim.HasAPI(UsdLux.LightAPI):  # type: ignore
                continue
            light = UsdLux.LightAPI(prim)
            old_color_attr = light.GetColorAttr().Get()
            if old_color_attr is None:
                old_color = (1.0, 1.0, 1.0)
            else:
                old_color = (old_color_attr[0], old_color_attr[1], old_color_attr[2])
            old_intensity = light.GetIntensityAttr().Get()
            if old_intensity is None:
                old_intensity = 1.0
            new_color, new_intensity = _rebalance(
                old_color, old_intensity, multiply[:3]
            )
            light.GetColorAttr().Set(new_color)
            light.GetIntensityAttr().Set(new_intensity)
            print(
                "[apply_to_stage] %s  branch=%s  multiply=%s  "
                "old=(color=%s, intensity=%.4f)  new=(color=%s, intensity=%.4f)"
                % (
                    prim.GetPath(),
                    branch.GetName(),
                    tuple(round(x, 4) for x in multiply[:3]),
                    tuple(round(x, 4) for x in old_color),
                    old_intensity,
                    tuple(round(x, 4) for x in new_color),
                    new_intensity,
                )
            )
            matched += 1

    return matched


def _build_leaf_to_branch_map(cooked_stage):
    """From a cooked stage, return {leaf_name: [(branch, final_path), ...]}
    for every UsdLuxLight under /lights/. Lets us answer "where does the
    light created at /lights/red end up?" by matching leaf names."""
    from pxr import UsdLux

    leaf_to_branches = {}
    for prim in cooked_stage.Traverse():
        if not prim.HasAPI(UsdLux.LightAPI):
            continue
        path_str = prim.GetPath().pathString
        if not path_str.startswith(LIGHTS_PREFIX):
            continue
        rest = path_str[len(LIGHTS_PREFIX) :]
        parts = rest.split("/")
        branch = parts[0]
        leaf = parts[-1]
        leaf_to_branches.setdefault(leaf, []).append((branch, path_str))
    return leaf_to_branches


def apply_to_lops(json_path=None):
    """Find Light LOPs and apply the JSON multiply for the branch each one
    ends up in. Branches are determined from the cooked /stage (so graft /
    restructure LOPs downstream are accounted for), then matched to LOPs by
    primpath leaf name. Each LOP's existing color is multiplied
    component-wise by the multiply and re-normalized; intensity absorbs the
    brightness factor."""
    if json_path is None:
        json_path = _resolve_default_json_path()

    edits = _load_edits(json_path)

    stage_root = hou.node("/stage")
    if not isinstance(stage_root, hou.LopNetwork):
        print("apply_to_lops: /stage is not a LOP network")
        return [], []
    display_node = stage_root.displayNode()
    if not isinstance(display_node, hou.LopNode):
        print("apply_to_lops: /stage has no display LOP")
        return [], []
    cooked_stage = display_node.stage()
    if cooked_stage is None:
        print("apply_to_lops: display LOP under /stage has no cooked stage")
        return [], []
    leaf_to_branches = _build_leaf_to_branch_map(cooked_stage)

    matched = []
    skipped = []
    light_lops_unmatched = []

    root = hou.node("/")
    if root is None:
        return matched, skipped

    for node in root.allSubChildren():
        if not isinstance(node, hou.LopNode):
            continue
        color_parm = _find_color_parm(node)
        if color_parm is None:
            continue
        primpath_parm = node.parm("primpath")
        primpath = str(primpath_parm.eval()) if primpath_parm is not None else ""
        if not primpath:
            light_lops_unmatched.append((node.path(), primpath, "no primpath value"))
            continue

        leaf = primpath.rstrip("/").split("/")[-1]
        candidates = leaf_to_branches.get(leaf, [])
        if not candidates:
            light_lops_unmatched.append(
                (
                    node.path(),
                    primpath,
                    "leaf '%s' is not a light in final stage" % leaf,
                )
            )
            continue
        if len(candidates) > 1:
            paths = [p for _, p in candidates]
            light_lops_unmatched.append(
                (node.path(), primpath, "leaf '%s' ambiguous: %s" % (leaf, paths))
            )
            continue
        branch, final_path = candidates[0]
        entry = edits.get(branch)
        if entry is None:
            light_lops_unmatched.append(
                (
                    node.path(),
                    primpath,
                    "branch '%s' (final %s) not in JSON" % (branch, final_path),
                )
            )
            continue
        multiply = entry.get("multiply")
        if not multiply or len(multiply) < 3:
            skipped.append((node.path(), "no multiply"))
            continue

        intensity_parm = _find_intensity_parm(node)
        old_color = color_parm.eval()
        old_intensity = intensity_parm.eval() if intensity_parm is not None else 1.0
        new_color, new_intensity = _rebalance(old_color, old_intensity, multiply[:3])

        color_parm.set(new_color)
        if intensity_parm is not None:
            intensity_parm.set(new_intensity)
            matched.append((node.path(), branch, final_path))
        else:
            skipped.append((node.path(), "color updated but no intensity parm"))

        print(
            "[apply_to_lops] %s (final %s)  branch=%s  multiply=%s  "
            "old=(color=%s, intensity=%.4f)  new=(color=%s, intensity=%.4f)"
            % (
                node.path(),
                final_path,
                branch,
                tuple(round(x, 4) for x in multiply[:3]),
                tuple(round(x, 4) for x in old_color),
                old_intensity,
                tuple(round(x, 4) for x in new_color),
                new_intensity,
            )
        )

    print("Updated %d light LOP(s):" % len(matched))
    for p, key, final in matched:
        print("  %s  (final %s)  <- %s" % (p, final, key))
    if skipped:
        print("Skipped %d:" % len(skipped))
        for p, reason in skipped:
            print("  %s  (%s)" % (p, reason))
    if light_lops_unmatched:
        print("\nLight LOPs found but not matched (%d):" % len(light_lops_unmatched))
        for p, primpath, reason in light_lops_unmatched:
            print("  %s  primpath=%r  (%s)" % (p, primpath, reason))
        print("(JSON branches available: %s)" % sorted(edits.keys()))
    return matched, skipped
