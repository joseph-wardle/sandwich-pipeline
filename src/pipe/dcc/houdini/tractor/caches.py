"""Stock File Cache nodes as the farm sees them: the caches upstream of one,
what a wedged parm reaches, and the frames each of a cache's tasks writes."""

from __future__ import annotations

import hou

from pipe.dcc.houdini.util.nodetypes import FILE_CACHES

LOAD_FROM_DISK = "loadfromdisk"
SIMULATION = "cachesim"
# The file name before the version, which Save to Disk hardens.
BASE_NAME = "basename"
# A File Cache builds its path from a base folder, unless it is given a file.
FILE_METHOD = "filemethod"
CONSTRUCTED = 0
BASE_FOLDER = "basedir"
FILE = "file"
# Start, end and increment, on the File Cache and on its ROP.
RANGE = "f"
# The ROP inside every File Cache that its Save to Disk button presses.
ROP = "render"
# The ROP's file parm, by its network type.
OUTPUTS = {"Sop": "sopoutput", "Lop": "lopoutput"}
# Whether the ROP writes a frame range, or only the current frame.
VALID_RANGE = "trange"
# The ROP's increment: the node's divided by its substeps.
INCREMENT = "f3"
# A step is a float; this keeps the last frame in when float steps fall just short of it.
STEP_TOLERANCE = 1e-6
# The networks whose output is their display node. A VOP or ROP network has
# none, and the /stage network's is the whole stage.
DISPLAYED = (hou.ObjNode, hou.SopNode, hou.DopNode, hou.LopNode)
# Frames per task of a cache that is not a simulation. Each task loads the hip
# again, so a chunk is several frames rather than one.
CHUNK = 10


def is_file_cache(node: hou.Node) -> bool:
    return node.type().nameWithCategory() in FILE_CACHES


def loads_from_disk(node: hou.Node) -> bool:
    return is_file_cache(node) and bool(node.evalParm(LOAD_FROM_DISK))


def sendable(node: hou.Node) -> bool:
    """A cache inside a locked asset can't be set to load from disk, so it is
    never offered as Cache first."""
    return is_file_cache(node) and not node.isInsideLockedHDA()


def upstream(node: hou.Node) -> list[hou.Node]:
    """The sendable File Caches `node` reads from, each after the caches it reads."""
    return [n for n in Walk(node).nodes if n != node and sendable(n)]


# A node of a walk by its session id, and whether the walk enters it.
Step = tuple[int, bool]


class Walk:
    """Every node `start` cooks from, each after the nodes it cooks from."""

    def __init__(self, start: hou.Node) -> None:
        self.nodes, self._steps = _walk(start)
        self._ids = {_id(node) for node in self.nodes}
        # The steps that cook from each step.
        self._readers: dict[Step, list[Step]] = {}
        for step, feeds in self._steps.items():
            for feed in feeds:
                self._readers.setdefault(feed, []).append(step)

    def __contains__(self, node: hou.Node) -> bool:
        return _id(node) in self._ids

    def readers(self, sources: list[hou.Node]) -> list[hou.Node]:
        """The walked nodes that cook from any of `sources`, the sources
        included, each after the nodes it cooks from."""
        wanted = {_id(source) for source in sources}
        todo = [step for step in self._steps if step[0] in wanted]
        reached = set(todo)
        while todo:
            for reader in self._readers.get(todo.pop(), []):
                if reader not in reached:
                    reached.add(reader)
                    todo.append(reader)
        ids = {node_id for node_id, _ in reached}
        return [node for node in self.nodes if _id(node) in ids]


def _walk(start: hou.Node) -> tuple[list[hou.Node], dict[Step, list[Step]]]:
    """Every node `start` cooks from, each after the nodes it cooks from, and
    every step of the walk, in the same order, with the steps it cooks from."""
    found: dict[int, hou.Node] = {}
    steps: dict[Step, list[Step]] = {}
    first: Step = (_id(start), True)
    seen = {first}
    stack = [(start, first, iter(_feeds(start, True, start)), [])]
    while stack:
        current, step, feeds, fed = stack[-1]
        nxt = next(feeds, None)
        if nxt is None:
            stack.pop()
            steps[step] = fed
            found.setdefault(step[0], current)
            continue
        node, enter = nxt
        key = (_id(node), enter)
        fed.append(key)
        if key not in seen:
            seen.add(key)
            stack.append((node, key, iter(_feeds(node, enter, start)), []))
    return list(found.values()), steps


def _feeds(node: hou.Node, enter: bool, start: hou.Node) -> list[tuple[hou.Node, bool]]:
    """What `node` cooks from, each with whether to enter it. A network holding
    the node it was reached from, or the walk's start, is downstream of its
    contents, so only its inputs are followed."""
    inputs = [n for n in node.inputs() if n is not None]
    if not enter:
        return [(n, not _within(start, n)) for n in inputs]
    found = inputs + list(node.references(include_children=False))
    # An asset cooks its output node, which needn't carry the display flag.
    found += node.subnetOutputs()
    display = node.displayNode() if isinstance(node, DISPLAYED) else None
    if display is not None:
        found.append(display)
    return [(n, not (_within(node, n) or _within(start, n))) for n in found]


def _id(node: hou.Node) -> int:
    # The stubs say sessionId() returns None. It returns the node's id.
    return node.sessionId()  # ty: ignore[invalid-return-type]


def _within(node: hou.Node, network: hou.Node) -> bool:
    return node == network or node.path().startswith(network.path() + "/")


def chunks(node: hou.Node) -> list[tuple[float, float]]:
    """The start and end frame of each task that writes `node`.

    A simulation is one task, since each frame cooks from the last, and so is
    a LOP cache, which writes every frame into one file. A cache that is not
    time dependent writes the scene's current frame.
    """
    # Every File Cache holds this ROP; hou types the child as optional.
    rop: hou.Node = node.node(ROP)  # ty: ignore[invalid-assignment]
    if not rop.evalParm(VALID_RANGE):
        return [(hou.frame(), hou.frame())]
    start, end, step = (float(v) for v in node.evalParmTuple(RANGE))
    if isinstance(node, hou.LopNode) or node.evalParm(SIMULATION):
        return [(start, end)]
    increment = float(rop.evalParm(INCREMENT))
    frames = times(start, end, step)
    found = []
    for i in range(0, len(frames), CHUNK):
        first, last = frames[i], frames[min(i + CHUNK, len(frames)) - 1]
        # A chunk also writes the substeps up to the next chunk's first frame.
        found.append((first, end if last == frames[-1] else last + step - increment))
    return found


def times(start: float, end: float, step: float) -> list[float]:
    """Every `step` from `start` to `end`, both included."""
    count = int((end - start) / step + STEP_TOLERANCE)
    return [start + i * step for i in range(count + 1)]
