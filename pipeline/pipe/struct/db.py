from __future__ import annotations

# We need to always import typing for defining the structs
# attrs doesn't support `|` syntax in 3.9
from typing import Any, Optional, Type, TypeVar

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
        **{  # type: ignore[arg-type]
            f.name: cattrs.gen.override(
                rename=f.metadata.get(_SG_NAME, None),
                struct_hook=f.metadata.get(_STRUCT_HOOK, None),
                unstruct_hook=f.metadata.get(_UNSTRUCT_HOOK, None),
            )
            for f in attrs.fields(cls)
        },
    ),
)


def normalize_display_name(name: Optional[str]) -> str:
    """Normalize a ShotGrid display name into a pipeline-safe name.

    Current rules:
    - lower-case the string
    - replace spaces with underscores
    """
    if not name:
        return ""
    return name.strip().lower().replace(" ", "_")


def normalize_subdirectory(subdirectory: Optional[str]) -> Optional[str]:
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


def build_asset_path(display_name: Optional[str], subdirectory: Optional[str]) -> str:
    """Build the canonical relative asset path.

    Result format:
      asset/<optional-subdirectory>/<normalized-asset-name>
    """
    asset_name = normalize_display_name(display_name) or "asset"
    path_parts = ["asset"]
    normalized_subdirectory = normalize_subdirectory(subdirectory)
    if normalized_subdirectory:
        path_parts.append(normalized_subdirectory)
    path_parts.append(asset_name)
    return "/".join(path_parts)


def validate_shot_code_token(shot_code: Optional[str]) -> str:
    """Validate a shot code for safe use as a single path token.

    Rules:
    - required (must be non-empty after trimming whitespace)
    - must not be "." or ".."
    - must not contain path separators ("/" or "\\")
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


def build_shot_path(shot_code: Optional[str]) -> str:
    """Build the canonical relative shot path: shot/<shot_code>."""
    return "/".join(("shot", validate_shot_code_token(shot_code)))


def _split_csv_set(value: Optional[str]) -> set[str]:
    """Parse a comma-separated ShotGrid string into normalized variant tokens."""
    if not value:
        return set()
    return {token.strip() for token in value.split(",") if token.strip()}


@attrs.define
class SGDiffable(Diffable):
    @classmethod
    def from_sg(cls: Type[_S], sg_dict: Optional[dict]) -> _S:
        if not sg_dict:
            raise TypeError(f"Cannot create {cls.__name__} from empty dict")
        return _con.structure(sg_dict, cls)

    def to_sg(self, exclude: list[str] = []) -> dict[str, Any]:
        """return dict in shotgun format from object"""
        data = _con.unstructure(self)
        result = {}

        for f in attrs.fields(self.__class__):
            if f.name in exclude:
                continue
            sg_key = f.metadata.get(_SG_NAME, f.name)
            val = data.get(f.name)
            if val is not None:
                # If there's an unstruct hook, apply it
                if hook := f.metadata.get(_UNSTRUCT_HOOK):
                    val = hook(val, None)
                result[sg_key] = val

        return result

    @classmethod
    def map_sg_field_names(cls: Type[attrs.AttrsInstance], name: str) -> str:
        """take SG name and map it to the field name on this class"""
        return next(
            (
                f.metadata.get(_SG_NAME, None) or f.name
                for f in attrs.fields(cls)
                if f.name == name
            ),
            "",
        )

    def sg_diff(self) -> dict[str, Any]:
        """Return a dict with changes made to the asset since it was
        initialized, in the form that ShotGrid expects"""
        sg_diff: dict[str, Any] = self.diff()
        for f in attrs.fields(self.__class__):
            if f.name in sg_diff:
                if hk := f.metadata.get(_UNSTRUCT_HOOK, None):
                    sg_diff[f.name] = hk(sg_diff[f.name], None)
                if nname := f.metadata.get(_SG_NAME, None):
                    sg_diff[nname] = sg_diff[f.name]
                    del sg_diff[f.name]
        return sg_diff


@attrs.define
class SGEntity(SGDiffable):
    code: Optional[str]
    id: int = field(on_setattr=attrs.setters.frozen)
    path: Optional[str] = field(
        default=None, kw_only=True, metadata={_SG_NAME: "sg_path"}
    )


@attrs.define
class SGEntityStub(SGDiffable):
    id: int


@attrs.frozen
class AssetStub(SGEntityStub):
    """Represent "stubs" that come from ShotGrid
    Stubs are JSON objects with 3 fields: id, name (display name), and type
    (which is always Asset in this case)
    """

    display_name: str = field(metadata={_SG_NAME: "name"})


@attrs.define
class Asset(SGEntity):
    type: str = field(metadata={_SG_NAME: "sg_asset_type"})
    subdirectory: Optional[str] = field(
        default=None,
        kw_only=True,
        metadata={
            _SG_NAME: "sg_subdirectory",
            _STRUCT_HOOK: lambda subdir, _: normalize_subdirectory(subdir),
            _UNSTRUCT_HOOK: lambda subdir, _: subdir or "",
        },
    )
    material_variants: set[str] = field(
        metadata={
            _SG_NAME: "sg_material_variants",
            _STRUCT_HOOK: lambda mv, _: _split_csv_set(mv),
            _UNSTRUCT_HOOK: lambda mv, _: ",".join(mv) if mv else "",
        }
    )
    geometry_variants: set[str] = field(
        metadata={
            _SG_NAME: "sg_geometry_variants",
            _STRUCT_HOOK: lambda mv, _: _split_csv_set(mv),
            _UNSTRUCT_HOOK: lambda mv, _: ",".join(mv) if mv else "",
        }
    )
    material_layers: set[str] = field(
        metadata={
            _SG_NAME: "sg_material_layers",
            _STRUCT_HOOK: lambda mv, _: _split_csv_set(mv),
            _UNSTRUCT_HOOK: lambda mv, _: ",".join(mv) if mv else "",
        }
    )
    parent: Optional[AssetStub] = (
        field(  # TODO see if we still need this for the new way of tracking variants
            metadata={
                _SG_NAME: "parents",
                _STRUCT_HOOK: lambda p, _: AssetStub.from_sg(p[0]) if len(p) else None,
                _UNSTRUCT_HOOK: lambda p, _: [p] if p else [],
            },
            on_setattr=attrs.setters.frozen,
        )
    )
    variants: list[AssetStub] = field(metadata={_SG_NAME: "assets"})
    version = None

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

    def __attrs_post_init__(self) -> None:
        self.subdirectory = normalize_subdirectory(self.subdirectory)
        self.path = self.asset_path
        super().__attrs_post_init__()

    def sg_diff(self) -> dict[str, Any]:
        """Return only ShotGrid fields that should be updated for Asset.

        Asset path is derived from display_name + subdirectory and should not
        write back to deprecated sg_path.
        """
        self.path = self.asset_path
        diff = super().sg_diff()
        diff.pop("path", None)
        diff.pop("sg_path", None)
        return diff


@attrs.define
class Environment(SGEntity):
    @property
    def display_name(self) -> str:
        """ShotGrid display name (code)."""
        return self.code or ""

    @property
    def name(self) -> str:
        """Normalized name derived from the ShotGrid display name."""
        return normalize_display_name(self.display_name)


@attrs.define
class EnvironmentStub(AssetStub):
    pass


@attrs.frozen
class SequenceStub(SGEntityStub):
    """Represent sequence "stubs" that come from ShotGrid"""

    code: str = field(metadata={_SG_NAME: "name"})


@attrs.define
class Sequence(SGEntity):
    code: str = field(on_setattr=attrs.setters.frozen)
    shots: list[ShotStub]
    set: Optional[EnvironmentStub] = field(
        default=None,
        metadata={
            _SG_NAME: "sg_set",
            _STRUCT_HOOK: lambda e, _: EnvironmentStub.from_sg(e) if e else None,
        },
    )
    sets: list[EnvironmentStub] = field(
        factory=list,
        metadata={
            _SG_NAME: "sg_sets",
            _STRUCT_HOOK: lambda raw_sets, _: [
                EnvironmentStub.from_sg(set) for set in (raw_sets or [])
            ],
        },
    )


@attrs.frozen
class ShotStub(SGEntityStub):
    """Represent shot "stubs" that come from ShotGrid"""

    code: str = field(metadata={_SG_NAME: "name"})

    @property
    def sg_ref(self) -> dict[str, Any]:
        return {"type": "Shot", "id": self.id}


@attrs.define
class Shot(SGEntity):
    assets: list[AssetStub] = field(
        metadata={_STRUCT_HOOK: lambda aa, _: [AssetStub.from_sg(a) for a in aa]}
    )
    code: str = field(on_setattr=attrs.setters.frozen)
    cut_in: int = field(metadata={_SG_NAME: "sg_cut_in"})
    cut_out: int = field(metadata={_SG_NAME: "sg_cut_out"})
    cut_duration: int = field(metadata={_SG_NAME: "sg_cut_duration"})
    sequence: Optional[SequenceStub] = field(
        metadata={
            _SG_NAME: "sg_sequence",
            _STRUCT_HOOK: lambda s, _: SequenceStub.from_sg(s) if s else None,
        }
    )
    set: Optional[EnvironmentStub] = field(
        default=None,
        metadata={
            _SG_NAME: "sg_set",
            _STRUCT_HOOK: lambda e, _: EnvironmentStub.from_sg(e) if e else None,
        },
    )
    sets: list[EnvironmentStub] = field(
        factory=list,
        metadata={
            _SG_NAME: "sg_sets",
            _STRUCT_HOOK: lambda raw_sets, _: [
                EnvironmentStub.from_sg(set) for set in (raw_sets or [])
            ],
        },
    )

    def __attrs_post_init__(self) -> None:
        self.path = build_shot_path(self.code)
        super().__attrs_post_init__()


@attrs.frozen
class UserStub(SGEntityStub):
    """Represent user "stubs" that come from ShotGrid"""

    name: str = field(metadata={_SG_NAME: "login"})


@attrs.define
class User(SGEntity):
    code: Optional[str] = field(init=False, repr=False, default=None)

    name: str = field(on_setattr=attrs.setters.frozen)

    login: Optional[str] = field(metadata={_SG_NAME: "login"})


@attrs.frozen
class TaskStub(SGEntityStub):
    """Represent shot "stubs" that come from ShotGrid"""

    id: int


@attrs.define
class Task(SGEntity):
    code: Optional[str] = field(init=False, repr=False, default=None)

    entity: ShotStub = field(metadata={_SG_NAME: "entity"})

    status: str = field(metadata={_SG_NAME: "sg_status_list"})

    content: str = field(metadata={_SG_NAME: "content"})


@attrs.define
class Version(SGEntity):
    code: str = field(on_setattr=attrs.setters.frozen)

    shot: ShotStub = field(
        metadata={
            _SG_NAME: "entity",
            _UNSTRUCT_HOOK: lambda val, _: (
                {"type": "Shot", "id": val["id"]}
                if isinstance(val, dict)
                else {"type": "Shot", "id": val.id}
            ),
        }
    )

    task: Task = field(
        metadata={
            _SG_NAME: "sg_task",
            _UNSTRUCT_HOOK: lambda val, _: (
                {"type": "Task", "id": val["id"]}
                if isinstance(val, dict)
                else {"type": "Task", "id": val.id}
            ),
        }
    )

    user: User = field(
        metadata={
            _SG_NAME: "user",
            _STRUCT_HOOK: lambda val, _: User.from_sg(val),
            _UNSTRUCT_HOOK: lambda val, _: (
                {"type": "HumanUser", "id": val["id"]}
                if isinstance(val, dict)
                else {"type": "HumanUser", "id": val.id}
            ),
        }
    )

    video_path: Optional[str] = field(metadata={_SG_NAME: "sg_path_to_frames"})

    description: Optional[str] = field(metadata={_SG_NAME: "description"})
