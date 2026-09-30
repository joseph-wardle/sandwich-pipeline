import nuke
import os
import re
import glob
import math
from functools import reduce
from pathlib import Path

from pipe.core import render


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


def version_sequences(version: Path) -> list[dict]:
    """
    The EXR sequence of each output folder of a version, or [] while it has none.

    A complete version can still hold no frames, such as one with only a movie.
    """
    found = []
    for folder in render.output_dirs(version):
        seq = _scan_exr_sequence(str(folder))
        if not seq:
            continue
        pattern, first, last, _pad, step = seq
        found.append(
            {
                "folder": folder.name,
                "pattern": pattern,
                "first": first,
                "last": last,
                "step": step,
            }
        )
    return found


def newest_readable(layer: Path) -> tuple[str, list[dict]] | None:
    """The name and sequences of the newest version with frames to read."""
    for version in render.versions(layer):
        if found := version_sequences(version):
            return version.name, found
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
        found = newest_readable(Path(layer_dir))
        if not found:
            continue
        version, seqs = found
        for seq in seqs:
            folder = seq["folder"]
            sequences.append(
                {
                    **seq,
                    # Legacy versions keep the one Read per layer they always had.
                    "name": layer
                    if folder in render.LEGACY_DIRS
                    else f"{layer}_{folder}",
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


def _hold_expr(first, last, step):
    """Expression that holds each frame of a stepped sequence until the next one."""
    return f"clamp({first}+{step}*floor((frame-{first})/{step}), {first}, {last})"


def read_sequence(read, s):
    """Point a Read at a sequence found by version_sequences."""
    read["file"].setValue(s["pattern"])
    # native sequence range
    read["origfirst"].setValue(s["first"])
    read["origlast"].setValue(s["last"])
    read["first"].setValue(s["first"])
    read["last"].setValue(s["last"])
    # Frames between those of a sequence on 2s or 4s are missing, and would
    # show black.
    hold = _hold_expr(s["first"], s["last"], s["step"]) if s["step"] > 1 else ""
    read["frame_mode"].setValue("expression")
    read["frame"].setValue(hold)


def make_read_nodes(render_subdir="render", node_name_prefix="EXR_read"):
    """
    Make or update one Read per output folder of each layer's newest readable version.

    - Nodes are named: <prefix>_<layer>_<output folder> (e.g., EXR_read_xpu_beauty)
    - A Read of that name reading an older version is pointed at the new one, keeping
      its wiring. One already reading the newest is left untouched, so the range and
      label the artist gave it survive a re-run.
    - Reads this tool made under another name are left as they are, and named in a
      message when they read anything but a newest version.
    - When a Read changed, the project frame range is set to the union
      [min(first), max(last)] across all sequences.
    - For sequences detected as rendered on 2s/4s (or any N-s cadence), the Read node's
      'frame' knob holds each frame until the next, so they play at the project's fps
      like any other.
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
    changed = False
    for s in sequences:
        node_name = f"{node_name_prefix}_{_sanitize_for_nuke(s['name'])}"
        # Every Send makes a new version, so artists re-run this often; a twin Read
        # would leave comp wired to the old one.
        read = nuke.toNode(node_name) or nuke.nodes.Read(
            name=node_name, on_error="black"
        )
        reads.append(read)
        # render/ is a link into /cache, and Reads hold either spelling.
        if os.path.realpath(read["file"].value()) == os.path.realpath(s["pattern"]):
            continue
        changed = True
        read_sequence(read, s)
        read["label"].setValue(f"{s['label']}  step:{s['step']}")

    if changed:
        nuke.root()["first_frame"].setValue(min(s["first"] for s in sequences))
        nuke.root()["last_frame"].setValue(max(s["last"] for s in sequences))

    notes = []
    if not changed:
        notes.append("Every Read already reads the newest version, so nothing changed.")
    if left := _left_behind(node_name_prefix, reads):
        lines = "\n".join(f"  {n.name()}: {n['file'].value()}" for n in left)
        notes.append(
            "These Reads read something other than the newest version, and were "
            f"left as they are:\n{lines}\nComp wired to them still shows those "
            "frames. Rewire it to the Reads of the newest version."
        )
    if notes:
        nuke.message("[Auto Read] " + "\n\n".join(notes))

    return reads


def _left_behind(node_name_prefix, reads):
    """
    Reads this tool made that it no longer updates.

    It once named Reads after the layer alone, and made a numbered twin on every run.
    """
    names = {read.name() for read in reads}
    newest = {_folder(read) for read in reads}
    return [
        node
        for node in nuke.allNodes("Read")
        if node.name().startswith(f"{node_name_prefix}_")
        and node.name() not in names
        and _folder(node) not in newest
    ]


def _folder(read):
    # render/ is a link into /cache, and Reads hold either spelling.
    return os.path.realpath(os.path.dirname(read["file"].value()))


def auto_read(render_subdir="render", node_name_prefix="EXR_read"):
    """Build the Read nodes and centre the node graph on the Viewer."""
    if not make_read_nodes(render_subdir, node_name_prefix):
        return
    if viewer := nuke.activeViewer():
        node = viewer.node()
        nuke.zoom(1, [node["xpos"].value(), node["ypos"].value()])
