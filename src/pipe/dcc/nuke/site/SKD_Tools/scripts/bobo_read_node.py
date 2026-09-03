import nuke
import os
import re
import glob
import math
from functools import reduce


def _find_images_dir(base):
    """Return images dir under base, preferring images_dn over images."""
    for sub in ("images_dn", "images"):
        p = os.path.join(base, sub)
        if os.path.isdir(p):
            return p
    return None


def _gcd_list(values):
    """Greatest common divisor for a list of positive integers."""
    if not values:
        return 1
    return reduce(math.gcd, values)


def _scan_exr_sequence(images_dir):
    """
    Return (pattern, first_frame, last_frame, pad, cadence_step)
    cadence_step is the GCD of frame gaps (1 = full, 2 = on 2s, 4 = on 4s, etc.)
    """
    files = glob.glob(os.path.join(images_dir, "*.exr"))
    if not files:
        return None

    nums, pad = [], 0
    for f in files:
        fname = os.path.basename(f)
        m = re.match(r"^(.*?)(\d+)\.exr$", fname)
        if not m:
            continue
        n = int(m.group(2))
        nums.append(n)
        pad = max(pad, len(m.group(2)))

    if not nums:
        return None

    nums = sorted(set(nums))
    first_frame = nums[0]
    last_frame = nums[-1]
    diffs = [b - a for a, b in zip(nums, nums[1:]) if b > a]
    step = _gcd_list(diffs) if diffs else 1
    if step <= 0:
        step = 1

    pattern = os.path.join(images_dir, "%%0%dd.exr" % pad)
    return pattern, first_frame, last_frame, pad, step


def get_latest_exr_sequences(render_root):
    """
    Discover EXR sequences in each render layer's most recent version.

    Returns a list of dicts:
      [{
        'label': <subfolder or 'root'>,
        'pattern': '/…/%04d.exr',
        'first': int,
        'last': int,
        'step': int   # 1 (full), 2 (on 2s), 4 (on 4s), etc.
      }, ...]
    """
    if not os.path.isdir(render_root):
        nuke.message(f"[Auto Read] Render folder does not exist:\n  {render_root}")
        return []

    version_pattern = re.compile(r"^V_(\d+)$", re.IGNORECASE)
    sequences = []

    for layer in sorted(os.listdir(render_root)):
        layer_dir = os.path.join(render_root, layer)
        # Skips hidden folders and stray files
        if layer.startswith(".") or not os.path.isdir(layer_dir):
            continue

        versions = []
        # Appends a tuple of the version number and the path to versions.
        for n in os.listdir(layer_dir):
            m = version_pattern.match(n)
            version_dir = os.path.join(layer_dir, n)
            if m and os.path.isdir(version_dir):
                versions.append((int(m.group(1)), version_dir))
        if not versions:
            continue

        images_dir = _find_images_dir(max(versions, key=lambda v: v[0])[1])
        if not images_dir:
            continue

        seq = _scan_exr_sequence(images_dir)
        if not seq:
            continue

        pattern, first, last, _pad, step = seq
        sequences.append(
            {
                "label": layer,
                "pattern": pattern,
                "first": first,
                "last": last,
                "step": step,
            }
        )
    if not sequences:
        nuke.message(f"[Auto Read] No sequences found in {render_root}")
        return []

    return sequences


def _sanitize_for_nuke(name):
    """Keep node names safe for Nuke."""
    safe = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    if safe and safe[0].isdigit():
        safe = "_" + safe
    return safe or "seq"


def _nearest_hold_expr(first, last, step):
    """
    Expression that maps timeline frame -> nearest existing frame in a stepped sequence.
    Uses floor(x + 0.5) to emulate round() for stability.
    """
    if step <= 1:
        # identity mapping (no missing frames cadence)
        return f"clamp(frame, {first}, {last})"
    # nearest multiple of 'step' from 'first', then clamp to [first,last]
    return f"clamp({first}+{step}*floor((frame-{first})/{step}+0.5), {first}, {last})"


def make_read_nodes(render_subdir="render", node_name_prefix="EXR_read"):
    """
    Create one Read node per discovered sequence inside the newest date/version folder.

    - Nodes are named: <prefix>_<label> (e.g., EXR_read_beauty)
    - Project frame range is set to the union [min(first), max(last)] across all sequences.
    - For sequences detected as rendered on 2s/4s (or any N-s cadence), the Read node's
      'frame' knob is set to hold the nearest available frame so playback never errors.
    - If ALL sequences share the same cadence of 2 or 4, project FPS is divided by that cadence.
    """
    script_path = nuke.root()["name"].value()
    # ex: /groups/sandwich/05_production/shot/A_010/comp/A_010.nk

    if not script_path or script_path == "Root":
        nuke.message("Open your shot before using Auto Read.")
        return []

    shot_dir = os.path.dirname(os.path.dirname(script_path))
    # ex: /groups/sandwich/05_production/shot/A_010
    render_dir = os.path.join(shot_dir, render_subdir)
    # ex: /groups/sandwich/05_production/shot/A_010/render

    sequences = get_latest_exr_sequences(render_dir)
    if not sequences:
        return []

    # This was originally implemented and works, but I found it personally annoying.

    # removes any existing nodes created by this tool (by prefix)
    # for n in nuke.allNodes("Read"):
    #     try:
    #         if n.name().startswith(node_name_prefix):
    #             nuke.delete(n)
    #     except Exception:
    #         pass

    created = []
    global_first = min(s["first"] for s in sequences)
    global_last = max(s["last"] for s in sequences)

    for s in sequences:
        label = _sanitize_for_nuke(s["label"])
        node_name = f"{node_name_prefix}_{label}"
        read = nuke.nodes.Read(name=node_name, file=s["pattern"], on_error="black")
        # native sequence range
        read["origfirst"].setValue(s["first"])
        read["origlast"].setValue(s["last"])
        read["first"].setValue(s["first"])
        read["last"].setValue(s["last"])

        # Time-map to handle sparse cadence (2s/4s/etc.) by holding nearest available frame
        expr = _nearest_hold_expr(s["first"], s["last"], s["step"])
        try:
            read["frame"].setExpression(expr)
        except Exception:
            # Fallback: if 'frame' knob is unavailable for some reason, do nothing
            pass

        # Optional label so it's obvious what's happening
        try:
            read["label"].setValue(f"{s['label']}  step:{s['step']}")
        except Exception:
            pass

        created.append(read)

    # Set the project frame range to cover all created sequences
    nuke.root()["first_frame"].setValue(global_first)
    nuke.root()["last_frame"].setValue(global_last)

    # If ALL sequences share cadence 2 or 4, adjust project FPS accordingly
    steps = set(s["step"] for s in sequences)
    if len(steps) == 1:
        only_step = steps.pop()
        if only_step in (2, 4):
            try:
                current_fps = float(nuke.root()["fps"].value())
                new_fps = current_fps / float(only_step)
                nuke.root()["fps"].setValue(new_fps)
                nuke.tprint(
                    f"[Auto Read] Detected cadence {only_step}s; FPS set to {new_fps:.3f}"
                )
            except Exception as e:
                nuke.tprint(f"[Auto Read] Could not adjust FPS: {e}")

    return created


def auto_read_latest_fx_exr():
    nodes = make_read_nodes("fx/render", node_name_prefix="Bobo_FX_read")
    if not nodes:
        return
    try:
        viewer = nuke.activeViewer().node()
        nuke.zoom(1, [viewer["xpos"].value(), viewer["ypos"].value()])
    except Exception as e:
        nuke.tprint(f"[Auto Read] Viewer zoom error: {e}")


def auto_read_latest_cfx_exr():
    nodes = make_read_nodes("cfx/render", node_name_prefix="Bobo_CFX_read")
    if not nodes:
        return
    try:
        viewer = nuke.activeViewer().node()
        nuke.zoom(1, [viewer["xpos"].value(), viewer["ypos"].value()])
    except Exception as e:
        nuke.tprint(f"[Auto Read] Viewer zoom error: {e}")


def auto_read_latest_exr():
    """
    Callback: build the Read nodes and zoom the Viewer.
    """
    nodes = make_read_nodes()
    if not nodes:
        return
    try:
        viewer = nuke.activeViewer().node()
        nuke.zoom(1, [viewer["xpos"].value(), viewer["ypos"].value()])
    except Exception as e:
        nuke.tprint(f"[Auto Read] Viewer zoom error: {e}")


def main():
    """
    Register the menu command and run it immediately.
    """
    nuke.menu("Nuke").addCommand(
        "Custom/Auto Read Latest EXR", auto_read_latest_exr, "ctrl+shift+r"
    )
    # immediately invoke to surface any errors at load time
    auto_read_latest_exr()


# auto-register on module load
try:
    main()
    nuke.tprint("[Auto Read] Module loaded and command registered.")
except Exception as e:
    nuke.tprint(f"[Auto Read] Failed to initialize: {e}")
