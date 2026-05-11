"""Entity classes for ShotGrid records.

Every Python object that represents a ShotGrid record lives here:
`Asset`, `Shot`, `Sequence`, `Environment`,
`User`, `Task`, `Version`, `Playlist`.  They are
constructed via `Entity.from_sg(sg_dict)` at the ShotGrid boundary and are
the only form ShotGrid data takes inside the pipeline.

Partial entities
----------------
When ShotGrid returns a linked reference — e.g. the sequence linked to a shot
— the dict carries only `{"type": "Sequence", "id": 3, "name": "a10"}`.
Those become *partial* entities: `id` and `code` are set, every other
field is `None`.  Reading any other field on a partial entity that is bound
to a `core.shotgrid.client.ShotGrid` connection lazily fetches the full
record from ShotGrid; see `SGEntity.__getattribute__`.

Equality and hashing
--------------------
Two entities of the same Python type with the same `id` are equal — whether
one is partial and the other fully fetched.  Sets and dicts deduplicate them
correctly.
"""

from __future__ import annotations

from typing import Any, TypeVar

import attrs
import cattrs
from attrs import field

from core.shotgrid.errors import ShotGridError
from core.shotgrid.paths import (
    build_asset_path,
    build_environment_path,
    build_shot_path,
    normalize_display_name,
    normalize_subdirectory,
    split_csv_set,
)

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
# Entity base class
# ---------------------------------------------------------------------------

# Identity fields are present on every entity (full or partial) by construction.
# Reading them must never trigger lazy hydration — that would recurse.
_HYDRATE_IDENTITY_FIELDS: frozenset[str] = frozenset({"id", "code"})

# Internal state owned by `SGEntity` itself.  Never overwritten when a
# partial entity hydrates and copies fields from its fresh counterpart.
_HYDRATE_COPY_SKIP: frozenset[str] = frozenset({"id", "_db", "_hydrated"})


@attrs.define(eq=False)
class SGEntity:
    """Base class for every ShotGrid entity.

    Equality is by `(type, id)` only: a partial entity (linked ref with
    only `id` + `code`) and a fully fetched entity with the same id are
    considered equal.

    When a caller reads any field that is `None` on a partial entity bound
    to a `core.shotgrid.client.ShotGrid` connection, the entity calls
    back to its connection, re-fetches itself, and fills in every field in
    place.  Hydration fires at most once per instance.

    This is an intentional exception to the rule that mutations should be
    explicit.  The alternative (forcing callers to remember which entities
    came from a linked ref) undermines self-documentation at call sites.  The
    back-reference is stripped on pickle.
    """

    id: int = field(kw_only=True, on_setattr=attrs.setters.frozen)
    code: str | None = field(default=None, kw_only=True)

    # Private back-reference to the ShotGrid connection that produced this
    # entity.  Typed `Any` to avoid a circular import with
    # `core.shotgrid.client`.
    _db: Any = field(init=False, default=None, eq=False, repr=False)
    _hydrated: bool = field(init=False, default=False, eq=False, repr=False)

    # ---- Construction from / to raw ShotGrid dicts ------------------------

    @classmethod
    def from_sg(cls: type[_S], sg_dict: dict | None) -> _S:
        """Structure a raw ShotGrid result row into an entity instance."""
        if not sg_dict:
            raise ValueError(
                f"Cannot create {cls.__name__} from an empty ShotGrid dict"
            )
        return _con.structure(sg_dict, cls)

    def to_sg(self, exclude: list[str] | None = None) -> dict[str, Any]:
        """Return this entity as a ShotGrid-shaped dict.

        `exclude` lists *Python* field names to omit from the output.
        """
        excluded = set(exclude or ())
        data = _con.unstructure(self)
        result: dict[str, Any] = {}
        for f in attrs.fields(self.__class__):
            if f.name in excluded or f.name.startswith("_"):
                continue
            sg_key = f.metadata.get(_SG_NAME, f.name)
            val = data.get(f.name)
            if val is None:
                continue
            if hook := f.metadata.get(_UNSTRUCT_HOOK):
                val = hook(val, None)
            result[sg_key] = val
        return result

    @classmethod
    def map_sg_field_names(cls: type[attrs.AttrsInstance], name: str) -> str:
        """Map a Python attribute name to its ShotGrid field name."""
        return next(
            (
                f.metadata.get(_SG_NAME, None) or f.name
                for f in attrs.fields(cls)
                if f.name == name
            ),
            "",
        )

    # ---- Identity, equality, lazy hydration -------------------------------

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
            # Read the raw slot value on `fresh` — `fresh` is also
            # db-attached so `getattr` would re-trigger its own lazy-fetch.
            object.__setattr__(self, f.name, object.__getattribute__(fresh, f.name))
        return object.__getattribute__(self, name)

    def __getstate__(self) -> dict[str, Any]:
        """Strip ``_db`` on pickle so serialized entities carry no live connection."""
        state = {f.name: getattr(self, f.name) for f in attrs.fields(type(self))}
        state["_db"] = None
        state["_hydrated"] = False
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

    All fields beyond `id` and `code` are `None` on a partial asset
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
            _STRUCT_HOOK: lambda mv, _: split_csv_set(mv),
            _UNSTRUCT_HOOK: lambda mv, _: ",".join(mv) if mv else "",
        },
    )
    geometry_variants: set[str] | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "sg_geometry_variants",
            _STRUCT_HOOK: lambda mv, _: split_csv_set(mv),
            _UNSTRUCT_HOOK: lambda mv, _: ",".join(mv) if mv else "",
        },
    )
    material_layers: set[str] | None = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "sg_material_layers",
            _STRUCT_HOOK: lambda mv, _: split_csv_set(mv),
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
    def path(self) -> str:
        """Alias for `asset_path`. Always derived; never stored."""
        return self.asset_path

    @property
    def tex_path(self) -> str:
        return f"{self.asset_path}/publish/tex/"

    @property
    def is_rigged(self) -> bool:
        return self.tags is not None and "SKD_rigged" in self.tags

    def __attrs_post_init__(self) -> None:
        self.subdirectory = normalize_subdirectory(self.subdirectory)


@attrs.define(eq=False)
class Environment(SGEntity):
    """A ShotGrid Environment (an `Asset` row with `sg_asset_type='Environment'`)."""

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

    @property
    def path(self) -> str:
        """Alias for `environment_path`. Always derived; never stored."""
        return self.environment_path

    def __attrs_post_init__(self) -> None:
        self.subdirectory = normalize_subdirectory(self.subdirectory)


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

    @property
    def path(self) -> str:
        """Alias for `shot_path`. Always derived; never stored."""
        return self.shot_path

    @property
    def frame_range(self) -> tuple[int, int]:
        """Inclusive `(cut_in, cut_out)` for this shot.

        Raises `ShotGridError` when either field is missing in
        ShotGrid — the message names the shot so artists know what to fix.
        """
        if self.cut_in is None or self.cut_out is None:
            raise ShotGridError(
                f"Shot {self.code!r} is missing cut_in/cut_out in ShotGrid."
            )
        return self.cut_in, self.cut_out


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
    """A ShotGrid Playlist — a named collection of Versions for review."""

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
    updated_at: Any | None = field(default=None, kw_only=True)
    created_at: Any | None = field(default=None, kw_only=True)
