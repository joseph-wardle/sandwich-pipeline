"""ShotGrid entity types and path helpers for the sandwich pipeline.

Every Python object that represents a ShotGrid entity lives here: ``Asset``,
``Shot``, ``Sequence``, ``Environment``, ``User``, ``Task``, ``Version``,
``Playlist``. They are constructed via ``Entity.from_sg(sg_dict)`` at the
ShotGrid boundary and are the only form data takes inside the pipeline.

Partial entities
----------------
When ShotGrid returns a linked reference — e.g. the sequence linked to a shot
— the dict carries only ``{"type": "Sequence", "id": 3, "name": "a10"}``.
Those become *partial* entities: ``id`` and ``code`` are set, everything else
is ``None``. Callers that need a full entity should re-fetch via
``db.get_<entity>(id=...)``. Phase 2 will add lazy auto-fetch.

Equality and hashing
--------------------
Two entities of the same Python type with the same ``id`` are equal, whether
one is partial and the other fully fetched.  Sets and dicts deduplicate them
correctly.

Stub compatibility
------------------
``SGEntityStub``, ``AssetStub``, ``ShotStub``, ``SequenceStub``,
``EnvironmentStub``, ``UserStub``, and ``TaskStub`` survive at the bottom of
this file so that ``pipe.db.sgaadb``, ``pipe.db.interface``, and a handful of
DCC helpers that have not yet been migrated keep importing cleanly.  Phase 4
deletes them alongside the old client.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, TypeVar

import attrs
import cattrs
from attrs import field

from pipe.struct.util import Diffable

_S = TypeVar("_S")

_SG_NAME = "sg_name"
_STRUCT_HOOK = "struct_hook"
_UNSTRUCT_HOOK = "unstruct_hook"
_con = cattrs.Converter()

_con.register_structure_hook_factory(
    attrs.has,
    lambda cls: cattrs.gen.make_dict_structure_fn(
        cls,
        _con,
        **{  # type: ignore
            f.name: cattrs.gen.override(
                rename=f.metadata.get(_SG_NAME, None),
                struct_hook=f.metadata.get(_STRUCT_HOOK, None),
                unstruct_hook=f.metadata.get(_UNSTRUCT_HOOK, None),
            )
            for f in attrs.fields(cls)
        },
    ),
)


# ---------------------------------------------------------------------------
# Path helpers (pure functions — no ShotGrid calls)
# ---------------------------------------------------------------------------


def normalize_display_name(name: str | None) -> str:
    """Normalize a ShotGrid display name into a pipeline-safe identifier.

    Steps: unicode normalize → encode ASCII → lowercase → spaces to underscores
    → strip non-alphanumeric characters.
    """
    if not name:
        return ""
    ascii_name = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    )
    normalized_name = ascii_name.strip().lower().replace(" ", "_")
    normalized_name = re.sub(r"[^a-z0-9_]", "", normalized_name)
    return normalized_name


def normalize_subdirectory(subdirectory: str | None) -> str | None:
    """Normalize and validate an asset subdirectory token.

    The subdirectory must be a single folder name (no path separators).
    """
    if subdirectory is None:
        return None
    normalized = str(subdirectory).strip()
    if not normalized:
        return None
    if normalized in {".", ".."}:
        raise ValueError("Asset subdirectory cannot be '.' or '..'")
    if "/" in normalized or "\\" in normalized:
        raise ValueError(
            "Asset subdirectory must be a single folder name without path separators"
        )
    return normalized


def build_asset_path(display_name: str | None, subdirectory: str | None) -> str:
    """Build the canonical relative asset path.

    Result format: ``asset/<optional-subdirectory>/<normalized-asset-name>``
    """
    asset_name = normalize_display_name(display_name) or "asset"
    path_parts = ["asset"]
    normalized_subdirectory = normalize_subdirectory(subdirectory)
    if normalized_subdirectory:
        path_parts.append(normalized_subdirectory)
    path_parts.append(asset_name)
    return "/".join(path_parts)


def build_environment_path(display_name: str | None, subdirectory: str | None) -> str:
    """Build the canonical relative environment path.

    Result format: ``set/<optional-subdirectory>/<normalized-environment-name>``
    """
    env_name = normalize_display_name(display_name) or "set"
    path_parts = ["set"]
    normalized_subdirectory = normalize_subdirectory(subdirectory)
    if normalized_subdirectory:
        path_parts.append(normalized_subdirectory)
    path_parts.append(env_name)
    return "/".join(path_parts)


def validate_shot_code_token(shot_code: str | None) -> str:
    """Validate a shot code for safe use as a single path token.

    Rules: required, non-empty, not ``.`` or ``..``, no path separators.
    """
    if shot_code is None:
        raise ValueError("Shot code is required")

    token = str(shot_code).strip()
    if not token:
        raise ValueError("Shot code cannot be empty")
    if token in {".", ".."}:
        raise ValueError("Shot code cannot be '.' or '..'")
    if "/" in token or "\\" in token:
        raise ValueError(
            "Shot code must be a single folder name without path separators"
        )

    return token


def build_shot_path(shot_code: str | None) -> str:
    """Build the canonical relative shot path: ``shot/<shot_code>``."""
    return "/".join(("shot", validate_shot_code_token(shot_code)))


def _split_csv_set(value: str | None) -> set[str]:
    """Parse a comma-separated ShotGrid string into normalized variant tokens."""
    if not value:
        return set()
    return {token.strip() for token in value.split(",") if token.strip()}


# ---------------------------------------------------------------------------
# Entity base classes
# ---------------------------------------------------------------------------


@attrs.define
class SGDiffable(Diffable):
    @classmethod
    def from_sg(cls: type[_S], sg_dict: dict | None) -> _S:
        if not sg_dict:
            raise TypeError(f"Cannot create {cls.__name__} from empty dict")
        return _con.structure(sg_dict, cls)

    def to_sg(self, exclude: list[str] = []) -> dict[str, Any]:
        """Return dict in ShotGrid format from this object."""
        data = _con.unstructure(self)
        result = {}

        for f in attrs.fields(self.__class__):
            if f.name in exclude:
                continue
            sg_key = f.metadata.get(_SG_NAME, f.name)
            val = data.get(f.name)
            if val is not None:
                if hook := f.metadata.get(_UNSTRUCT_HOOK):
                    val = hook(val, None)
                result[sg_key] = val

        return result

    @classmethod
    def map_sg_field_names(cls: type[attrs.AttrsInstance], name: str) -> str:
        """Map a local field name to its ShotGrid key."""
        return next(
            (
                f.metadata.get(_SG_NAME, None) or f.name
                for f in attrs.fields(cls)
                if f.name == name
            ),
            "",
        )

    def sg_diff(self) -> dict[str, Any]:
        """Return only changed fields in ShotGrid key format, ready for ``sg.update``."""
        sg_diff: dict[str, Any] = self.diff()
        for f in attrs.fields(self.__class__):
            if f.name in sg_diff:
                if hk := f.metadata.get(_UNSTRUCT_HOOK, None):
                    sg_diff[f.name] = hk(sg_diff[f.name], None)
                if nname := f.metadata.get(_SG_NAME, None):
                    sg_diff[nname] = sg_diff[f.name]
                    del sg_diff[f.name]
        return sg_diff


# Never triggers lazy hydration: partials always carry these by construction,
# or they are derivable in __attrs_post_init__ from fields that are carried.
_HYDRATE_IDENTITY_FIELDS: frozenset[str] = frozenset({"id", "code", "path"})

# Never overwritten during hydration: id is invariant; the rest is internal
# state owned by this base class or by Diffable.
_HYDRATE_COPY_SKIP: frozenset[str] = frozenset(
    {
        "id",
        "_db",
        "_hydrated",
        "_initial_state",
    }
)


@attrs.define(eq=False)
class SGEntity(SGDiffable):
    """Base class for every ShotGrid entity.

    Equality is by ``(type, id)`` only: a partial entity (linked ref with
    only ``id`` + ``code``) and a fully fetched entity with the same id are
    considered equal.

    When a caller reads any other field while ``None``, the entity calls
    back to its ``ShotGrid`` connection, re-fetches itself, and fills in
    every field in place. Hydration fires at most once per instance.

    This is an intentional exception to the rule that all mutations should be
    explicit. The alternative (forcing callers to remember which
    entities came from a linked ref) undermines  self-documentation
    at call sites.  The back-reference is stripped on pickle.
    """

    id: int = field(kw_only=True, on_setattr=attrs.setters.frozen)
    code: str | None = field(default=None, kw_only=True)
    path: str | None = field(default=None, kw_only=True, metadata={_SG_NAME: "sg_path"})

    # Private back-reference to the ShotGrid connection that produced this
    # entity.  Typed ``Any`` to avoid a circular import — ``pipe.db.shotgrid``
    # already imports every entity class from here.  ``CODING_STANDARD.md``
    # §"Localize dynamic boundaries" permits this narrow Any.
    _db: Any = field(init=False, default=None, eq=False, repr=False)
    _hydrated: bool = field(init=False, default=False, eq=False, repr=False)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SGEntity):
            return NotImplemented
        return type(self) is type(other) and self.id == other.id

    def __hash__(self) -> int:
        return hash((type(self).__name__, self.id))

    def __getattribute__(self, name: str) -> Any:
        value = object.__getattribute__(self, name)
        if value is not None:
            return value
        if name.startswith("_") or name in _HYDRATE_IDENTITY_FIELDS:
            return value
        db = object.__getattribute__(self, "_db")
        if db is None or object.__getattribute__(self, "_hydrated"):
            return value
        # Set the flag before fetching so any reentry short-circuits.
        object.__setattr__(self, "_hydrated", True)
        fresh = db.reload(self)
        for f in attrs.fields(type(self)):
            if f.name in _HYDRATE_COPY_SKIP:
                continue
            # Read the raw slot value on ``fresh`` — ``fresh`` is also
            # db-attached so ``getattr`` would re-trigger its own lazy-fetch.
            object.__setattr__(self, f.name, object.__getattribute__(fresh, f.name))
        return object.__getattribute__(self, name)

    def __getstate__(self) -> dict[str, Any]:
        """Strip ``_db`` on pickle so serialized entities carry no live connection."""
        state = {f.name: getattr(self, f.name) for f in attrs.fields(type(self))}
        state["_db"] = None
        state["_hydrated"] = False
        state["_initial_state"] = getattr(self, "_initial_state", {})
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        for name, value in state.items():
            object.__setattr__(self, name, value)


# ---------------------------------------------------------------------------
# Entity classes
# ---------------------------------------------------------------------------


@attrs.define(eq=False)
class Asset(SGEntity):
    """A ShotGrid Asset — character, prop, set dressing item, etc.

    All fields beyond ``id`` and ``code`` are ``None`` on a partial asset
    (one that arrived as a linked reference from another entity).
    """

    type: str | None = field(
        default=None,
        kw_only=True,
        metadata={_SG_NAME: "sg_asset_type"},
    )
    tags: set[str] | None = field(
        default=None,
        kw_only=True,
        on_setattr=attrs.setters.frozen,
        metadata={
            _SG_NAME: "tags",
            _STRUCT_HOOK: lambda tags, _: (
                set(tag["name"] for tag in tags) if tags is not None else None
            ),
        },
    )
    subdirectory: str | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "sg_subdirectory",
            _STRUCT_HOOK: lambda subdir, _: normalize_subdirectory(subdir),
            _UNSTRUCT_HOOK: lambda subdir, _: subdir or "",
        },
    )
    material_variants: set[str] | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "sg_material_variants",
            _STRUCT_HOOK: lambda mv, _: _split_csv_set(mv),
            _UNSTRUCT_HOOK: lambda mv, _: ",".join(mv) if mv else "",
        },
    )
    geometry_variants: set[str] | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "sg_geometry_variants",
            _STRUCT_HOOK: lambda mv, _: _split_csv_set(mv),
            _UNSTRUCT_HOOK: lambda mv, _: ",".join(mv) if mv else "",
        },
    )
    material_layers: set[str] | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "sg_material_layers",
            _STRUCT_HOOK: lambda mv, _: _split_csv_set(mv),
            _UNSTRUCT_HOOK: lambda mv, _: ",".join(mv) if mv else "",
        },
    )

    @property
    def display_name(self) -> str:
        """ShotGrid display name (code)."""
        return self.code or ""

    @property
    def name(self) -> str:
        """Normalized name derived from the ShotGrid display name."""
        return normalize_display_name(self.display_name)

    @property
    def asset_path(self) -> str:
        """Canonical relative path for this asset."""
        return build_asset_path(self.display_name, self.subdirectory)

    @property
    def tex_path(self) -> str:
        return f"{self.asset_path}/publish/tex/"

    @property
    def is_rigged(self) -> bool:
        return self.tags is not None and "SKD_rigged" in self.tags

    def __attrs_post_init__(self) -> None:
        self.subdirectory = normalize_subdirectory(self.subdirectory)
        self.path = self.asset_path
        super().__attrs_post_init__()

    def sg_diff(self) -> dict[str, Any]:
        """Return only changed ShotGrid fields for this asset.

        Asset path is derived and must never write back to the deprecated
        ``sg_path`` field.
        """
        self.path = self.asset_path
        diff = super().sg_diff()
        diff.pop("tags", None)
        diff.pop("path", None)
        diff.pop("sg_path", None)
        return diff


@attrs.define(eq=False)
class Environment(SGEntity):
    """A ShotGrid Environment (custom entity representing a set / location)."""

    subdirectory: str | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "sg_subdirectory",
            _STRUCT_HOOK: lambda subdir, _: normalize_subdirectory(subdir),
            _UNSTRUCT_HOOK: lambda subdir, _: subdir or "",
        },
    )

    @property
    def display_name(self) -> str:
        """ShotGrid display name (code)."""
        return self.code or ""

    @property
    def name(self) -> str:
        """Normalized name derived from the ShotGrid display name."""
        return normalize_display_name(self.display_name)

    @property
    def environment_path(self) -> str:
        """Canonical relative path for this environment."""
        return build_environment_path(self.display_name, self.subdirectory)

    def __attrs_post_init__(self) -> None:
        self.subdirectory = normalize_subdirectory(self.subdirectory)
        self.path = self.environment_path
        super().__attrs_post_init__()

    def sg_diff(self) -> dict[str, Any]:
        """Return only changed ShotGrid fields for this environment.

        Environment path is derived and must never write back to ``sg_path``.
        """
        self.path = self.environment_path
        diff = super().sg_diff()
        diff.pop("path", None)
        diff.pop("sg_path", None)
        return diff


@attrs.define(eq=False)
class User(SGEntity):
    """A ShotGrid HumanUser."""

    code: str | None = field(init=False, repr=False, default=None)

    name: str | None = field(default=None, on_setattr=attrs.setters.frozen)

    login: str | None = field(default=None, kw_only=True, metadata={_SG_NAME: "login"})


@attrs.define(eq=False)
class Sequence(SGEntity):
    """A ShotGrid Sequence — a group of shots."""

    code: str | None = field(default=None, on_setattr=attrs.setters.frozen)
    shots: list[Shot] | None = field(
        default=None,
        kw_only=True,
        metadata={
            _STRUCT_HOOK: lambda ss, _: (
                [Shot(id=s["id"], code=s.get("name")) for s in ss]
                if ss is not None
                else None
            ),
        },
    )
    set: Environment | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "sg_set",
            _STRUCT_HOOK: lambda e, _: (
                Environment(id=e["id"], code=e.get("name")) if e else None
            ),
        },
    )
    sets: list[Environment] | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "sg_sets",
            _STRUCT_HOOK: lambda raw_sets, _: (
                [Environment(id=e["id"], code=e.get("name")) for e in raw_sets]
                if raw_sets is not None
                else None
            ),
        },
    )


@attrs.define(eq=False)
class Shot(SGEntity):
    """A ShotGrid Shot — a single camera cut within a sequence."""

    code: str | None = field(default=None, on_setattr=attrs.setters.frozen)
    assets: list[Asset] | None = field(
        default=None,
        kw_only=True,
        metadata={
            _STRUCT_HOOK: lambda aa, _: (
                [Asset(id=a["id"], code=a.get("name")) for a in aa]
                if aa is not None
                else None
            ),
        },
    )
    cut_in: int | None = field(
        default=None, kw_only=True, metadata={_SG_NAME: "sg_cut_in"}
    )
    cut_out: int | None = field(
        default=None, kw_only=True, metadata={_SG_NAME: "sg_cut_out"}
    )
    cut_duration: int | None = field(
        default=None, kw_only=True, metadata={_SG_NAME: "sg_cut_duration"}
    )
    sequence: Sequence | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "sg_sequence",
            _STRUCT_HOOK: lambda s, _: (
                Sequence(id=s["id"], code=s.get("name")) if s else None
            ),
        },
    )
    set: Environment | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "sg_set",
            _STRUCT_HOOK: lambda e, _: (
                Environment(id=e["id"], code=e.get("name")) if e else None
            ),
        },
    )
    sets: list[Environment] | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "sg_sets",
            _STRUCT_HOOK: lambda raw_sets, _: (
                [Environment(id=e["id"], code=e.get("name")) for e in raw_sets]
                if raw_sets is not None
                else None
            ),
        },
    )

    @property
    def shot_path(self) -> str:
        """Canonical relative path for this shot: ``shot/<shot_code>``."""
        return build_shot_path(self.code)

    def __attrs_post_init__(self) -> None:
        # Skip path derivation on partial shots that arrived as linked refs
        # without a name — validate_shot_code_token would reject ``None``.
        if self.code is not None:
            self.path = self.shot_path
        super().__attrs_post_init__()

    def sg_diff(self) -> dict[str, Any]:
        """Return only changed ShotGrid fields for this shot.

        Shot path is derived from ``code`` and must never write to ``sg_path``.
        """
        if self.code is not None:
            self.path = self.shot_path
        diff = super().sg_diff()
        diff.pop("path", None)
        diff.pop("sg_path", None)
        return diff


@attrs.define(eq=False)
class Task(SGEntity):
    """A ShotGrid Task — a unit of work assigned to a user on a shot or asset."""

    code: str | None = field(init=False, repr=False, default=None)

    entity: Shot | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "entity",
            _STRUCT_HOOK: lambda s, _: (
                Shot(id=s["id"], code=s.get("name")) if s else None
            ),
        },
    )
    status: str | None = field(
        default=None, kw_only=True, metadata={_SG_NAME: "sg_status_list"}
    )
    content: str | None = field(
        default=None, kw_only=True, metadata={_SG_NAME: "content"}
    )


@attrs.define(eq=False)
class Version(SGEntity):
    """A ShotGrid Version — a review-able media upload linked to a shot or asset."""

    code: str | None = field(default=None, on_setattr=attrs.setters.frozen)

    shot: Shot | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "entity",
            _STRUCT_HOOK: lambda s, _: (
                Shot(id=s["id"], code=s.get("name")) if s else None
            ),
            _UNSTRUCT_HOOK: lambda val, _: (
                {"type": "Shot", "id": val["id"]}
                if isinstance(val, dict)
                else {"type": "Shot", "id": val.id}
            ),
        },
    )

    task: Task | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "sg_task",
            _STRUCT_HOOK: lambda t, _: (Task(id=t["id"]) if t else None),
            _UNSTRUCT_HOOK: lambda val, _: (
                {"type": "Task", "id": val["id"]}
                if isinstance(val, dict)
                else {"type": "Task", "id": val.id}
            ),
        },
    )

    user: User | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "user",
            _STRUCT_HOOK: lambda val, _: User.from_sg(val) if val else None,
            _UNSTRUCT_HOOK: lambda val, _: (
                {"type": "HumanUser", "id": val["id"]}
                if isinstance(val, dict)
                else {"type": "HumanUser", "id": val.id}
            ),
        },
    )

    video_path: str | None = field(
        default=None, kw_only=True, metadata={_SG_NAME: "sg_path_to_frames"}
    )
    description: str | None = field(
        default=None, kw_only=True, metadata={_SG_NAME: "description"}
    )


@attrs.define(eq=False)
class Playlist(SGEntity):
    """A ShotGrid Playlist — a named collection of Versions for review.

    Promoted from the Phase 0 placeholder in ``pipe.db.shotgrid`` to a full
    ``SGEntity`` with ShotGrid field metadata.
    """

    sg_status_list: str | None = field(
        default=None,
        kw_only=True,
        metadata={_SG_NAME: "sg_status_list"},
    )
    versions: list[Version] | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "versions",
            _STRUCT_HOOK: lambda vv, _: (
                [Version(id=v["id"], code=v.get("name")) for v in vv]
                if vv is not None
                else None
            ),
        },
    )


# ---------------------------------------------------------------------------
# Phase 4 deletes everything below this banner.
#
# These stub classes exist only so that ``pipe.db.sgaadb``, ``pipe.db.interface``,
# ``pipe.db.typing``, ``pipe.h.animpostprocess``, and ``pipe.h.hipfile.shot``
# keep importing cleanly while the old client is still in use.  They are NOT
# part of the new API.  Do not use them in new code.
# ---------------------------------------------------------------------------


@attrs.define
class SGEntityStub(SGDiffable):
    id: int


@attrs.frozen
class AssetStub(SGEntityStub):  # ty: ignore[invalid-frozen-dataclass-subclass]
    """Represent "stubs" that come from ShotGrid."""

    display_name: str = field(metadata={_SG_NAME: "name"})


@attrs.define
class EnvironmentStub(AssetStub):  # ty: ignore[invalid-frozen-dataclass-subclass]
    pass


@attrs.frozen
class SequenceStub(SGEntityStub):  # ty: ignore[invalid-frozen-dataclass-subclass]
    """Represent sequence "stubs" that come from ShotGrid."""

    code: str = field(metadata={_SG_NAME: "name"})


@attrs.frozen
class ShotStub(SGEntityStub):  # ty: ignore[invalid-frozen-dataclass-subclass]
    """Represent shot "stubs" that come from ShotGrid."""

    code: str = field(metadata={_SG_NAME: "name"})

    @property
    def sg_ref(self) -> dict[str, Any]:
        return {"type": "Shot", "id": self.id}


@attrs.frozen
class UserStub(SGEntityStub):  # ty: ignore[invalid-frozen-dataclass-subclass]
    """Represent user "stubs" that come from ShotGrid."""

    name: str = field(metadata={_SG_NAME: "login"})


@attrs.frozen
class TaskStub(SGEntityStub):  # ty: ignore[invalid-frozen-dataclass-subclass]
    """Represent task "stubs" that come from ShotGrid."""

    id: int
