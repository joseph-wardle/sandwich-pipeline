import nuke
import os
import re
import glob
import math
from functools import reduce


# Tractor's Cleanup writes this once it has found every output of a version.
COMPLETE = "complete"
# Tractor's scratch, which Cleanup empties.
TMP = "tmp"
# Where frames went before Tractor gave each output a folder of its own. fx2d still
# delivers into images/.
LEGACY_DIRS = ("images_dn", "images")
# The spelling Tractor Configure gives the version folders it renders into.
VERSION = re.compile(r"^v(\d+)$")


def versions(layer_dir: str) -> list[tuple[str, str]]:
    """(name, path) of each version folder of a render layer, newest first."""
    found = [
        (int(m.group(1)), name, path)
        for name in os.listdir(layer_dir)
        if (m := VERSION.match(name))
        and os.path.isdir(path := os.path.join(layer_dir, name))
    ]
    return [(name, path) for _, name, path in sorted(found, reverse=True)]


def output_dirs(version_dir: str) -> list[str]:
    """The folders of a version comp should read, or [] while it has none."""
    if os.path.isfile(os.path.join(version_dir, COMPLETE)):
        return [
            path
            for name in sorted(os.listdir(version_dir))
            if name != TMP and os.path.isdir(path := os.path.join(version_dir, name))
        ]
    # Versions from before Tractor marked them complete, and fx2d deliveries.
    for name in LEGACY_DIRS:
        path = os.path.join(version_dir, name)
        if os.path.isdir(path):
            return [path]
    return []


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


def version_sequences(version_dir: str) -> list[dict]:
    """
    The EXR sequence of each output folder of a version, or [] while it has none.

    A complete version can still hold no frames, such as one with only a movie.
    """
    found = []
    for images_dir in output_dirs(version_dir):
        seq = _scan_exr_sequence(images_dir)
        if not seq:
            continue
        pattern, first, last, _pad, step = seq
        found.append(
            {
                "folder": os.path.basename(images_dir),
                "pattern": pattern,
                "first": first,
                "last": last,
                "step": step,
            }
        )
    return found


def _newest_readable(layer_dir: str) -> tuple[str, list[dict]] | None:
    # A version folder exists from the moment its job is sent, so the newest
    # may have nothing to read yet.
    for version, path in versions(layer_dir):
        if found := version_sequences(path):
            return version, found
    return None


def get_latest_exr_sequences(render_root):
    """
    Discover EXR sequences in each render layer's newest version with frames.

    Returns a list of dicts, one per output folder:
      [{
        'name': <layer>_<output folder>, or <layer> for a legacy version,
        'label': '<layer> v### <output folder>',
        'pattern': '/…/%04d.exr',
        'first': int,
        'last': int,
        'step': int   # 1 (full), 2 (on 2s), 4 (on 4s), etc.
      }, ...]
    """
    if not os.path.isdir(render_root):
        nuke.message(f"[Auto Read] Render folder does not exist:\n  {render_root}")
        return []

    sequences = []
    for layer in sorted(os.listdir(render_root)):
        layer_dir = os.path.join(render_root, layer)
        # Skips hidden folders and stray files
        if layer.startswith(".") or not os.path.isdir(layer_dir):
            continue
        found = _newest_readable(layer_dir)
        if not found:
            continue
        version, seqs = found
        for seq in seqs:
            folder = seq["folder"]
            sequences.append(
                {
                    **seq,
                    # Legacy versions keep the one Read per layer they always had.
                    "name": layer if folder in LEGACY_DIRS else f"{layer}_{folder}",
                    "label": f"{layer} {version} {folder}",
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
    Make or update one Read per output folder of each layer's newest readable version.

    - Nodes are named: <prefix>_<layer>_<output folder> (e.g., EXR_read_xpu_beauty)
    - A Read of that name that already exists is pointed at the new version, keeping
      its wiring and the artist's settings.
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

    reads = []
    global_first = min(s["first"] for s in sequences)
    global_last = max(s["last"] for s in sequences)

    for s in sequences:
        node_name = f"{node_name_prefix}_{_sanitize_for_nuke(s['name'])}"
        # Every Send makes a new version, so artists re-run this often; a twin Read
        # would leave comp wired to the old one.
        read = nuke.toNode(node_name) or nuke.nodes.Read(
            name=node_name, on_error="black"
        )
        read["file"].setValue(s["pattern"])
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

        reads.append(read)

    # Set the project frame range to cover all sequences
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

    return reads


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
