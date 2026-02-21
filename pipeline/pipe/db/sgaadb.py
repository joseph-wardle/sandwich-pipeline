from __future__ import annotations

import logging
import os
import threading
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from functools import partialmethod as pm
from typing import TYPE_CHECKING, Optional

from pipe.struct.db import (
    Asset,
    Environment,
    Sequence,
    SGEntity,
    SGEntityStub,
    Shot,
    ShotStub,
    Task,
    User,
    Version,
    build_asset_path,
    normalize_display_name,
)

if TYPE_CHECKING:
    import typing
    from typing import Any, Callable, Iterable, Optional

    from typing_extensions import Unpack

    from .typing import *  # noqa: F403
    from .typing import AttrMappingKwargs, Filter

from . import shotgun_api3
from .interface import DBInterface

log = logging.getLogger(__name__)


@dataclass(eq=True, frozen=True)
class SG_Config:
    project_id: int
    # DO NOT SHARE/COMMIT THE sg_key!!! IT'S EQUIVALENT TO AN ADMIN PW!!!
    sg_key: str
    sg_script: str
    sg_server: str


class SGaaDB(DBInterface):
    """ShotGrid as a Database"""

    _sg: shotgun_api3.Shotgun
    _id: int
    _sg_entity_lists: dict[str, list[dict]]
    _cache_lock: threading.Lock
    _update_notifier: threading.Condition
    _update_thread: threading.Thread

    _conn_instances: dict[SG_Config, SGaaDB] = {}

    @classmethod
    def Get(cls, config: SG_Config) -> SGaaDB:
        if config in cls._conn_instances:
            return cls._conn_instances[config]
        else:
            log.debug("Creating new DB instance.")
            cls._conn_instances[config] = cls(config)
            return cls._conn_instances[config]

    def __init__(self, config: SG_Config) -> None:
        self._sg = shotgun_api3.Shotgun(
            config.sg_server, config.sg_script, config.sg_key
        )
        self._id = config.project_id

        self._cache_lock = threading.Lock()
        self._update_notifier = threading.Condition()

        self._sg_entity_lists = {}
        self._load_sg_asset_list()
        self._load_sg_user_list()
        self._load_sg_env_list()
        self._load_sg_sequence_list()
        self._load_sg_shot_list()

        self._update_thread = threading.Thread(
            target=self._threaded_updater, daemon=True
        )
        self._update_thread.start()

    def _threaded_updater(self) -> None:
        while True:
            with self._update_notifier:
                # wait until the cache is manually expired or timeout (5 min) reached
                try:
                    self._update_notifier.wait(timeout=300)
                except TimeoutError:
                    pass

                log.debug("Cache expired, refreshing list")
                # sequences and environments don't update freqently, so we
                #   just pull them once
                self._load_sg_asset_list()
                self._load_sg_shot_list()

    def _load_sg_asset_list(self) -> None:
        """Load the list of assets from SG to local cache"""
        with self._cache_lock:
            query = _AssetListQuery(self._id)
            asset_list = query.exec(self._sg)
            self._sg_entity_lists[Asset.__name__] = asset_list
            self._log_asset_name_collisions(asset_list)

    @staticmethod
    def _log_asset_name_collisions(asset_list: list[dict]) -> None:
        """Log collisions where display names normalize to the same asset.name."""
        normalized_names: dict[str, list[tuple[Optional[int], Optional[str]]]] = (
            defaultdict(list)
        )
        for asset in asset_list:
            code = asset.get("code")
            normalized = normalize_display_name(code)
            if not normalized:
                continue
            normalized_names[normalized].append((asset.get("id"), code))

        for normalized, entries in normalized_names.items():
            if len(entries) < 2:
                continue
            details = ", ".join(
                f"id={asset_id} code={code!r}" for asset_id, code in entries
            )
            log.error(
                "Asset name collision after normalization: name=%r assets=[%s]",
                normalized,
                details,
            )

    @staticmethod
    def _normalize_relative_path(path: str) -> str:
        return path.replace("\\", "/").strip("/")

    @staticmethod
    def _canonical_asset_path_from_sg(asset: dict) -> str:
        try:
            return build_asset_path(asset.get("code"), asset.get("sg_subdirectory"))
        except ValueError as exc:
            log.error(
                "Invalid asset subdirectory in ShotGrid (id=%s code=%r subdirectory=%r): %s",
                asset.get("id"),
                asset.get("code"),
                asset.get("sg_subdirectory"),
                exc,
            )
            return build_asset_path(asset.get("code"), None)

    def _asset_matches_path(self, asset: dict, target_path: str) -> bool:
        canonical = self._normalize_relative_path(
            self._canonical_asset_path_from_sg(asset)
        )
        if canonical == target_path:
            return True

        legacy_path = asset.get("sg_path")
        if isinstance(legacy_path, str) and legacy_path.strip():
            return self._normalize_relative_path(legacy_path) == target_path
        return False

    def _load_sg_user_list(self) -> None:
        """Load the list of assets from SG to local cache"""
        with self._cache_lock:
            query = _UserListQuery(self._id)
            self._sg_entity_lists[User.__name__] = query.exec(self._sg)

    def _load_sg_env_list(self) -> None:
        """Load the list of environments from SG to local cache"""
        with self._cache_lock:
            query = _EnvironmentListQuery(self._id)
            self._sg_entity_lists[Environment.__name__] = query.exec(self._sg)

    def _load_sg_sequence_list(self) -> None:
        """Load the list of sequences from SG to local cache"""
        with self._cache_lock:
            query = _SequenceListQuery(self._id)
            self._sg_entity_lists[Sequence.__name__] = query.exec(self._sg)

    def _load_sg_shot_list(self) -> None:
        """Load the list of shots from SG to local cache"""
        with self._cache_lock:
            query = _ShotListQuery(self._id)
            self._sg_entity_lists[Shot.__name__] = query.exec(self._sg)

    def expire_cache(self) -> None:
        with self._update_notifier:
            self._update_notifier.notify()

    def get_entity_by_attr(
        self, entity_type: type[SGEntity], attr: str, attr_val: str | int
    ) -> SGEntity:
        if entity_type is Asset and attr == "path":
            target = self._normalize_relative_path(str(attr_val))
            return Asset.from_sg(
                next(
                    e
                    for e in self._sg_entity_lists[Asset.__name__]
                    if self._asset_matches_path(e, target)
                )
            )

        internal_attr = entity_type.map_sg_field_names(attr)
        return entity_type.from_sg(
            next(
                e
                for e in self._sg_entity_lists[entity_type.__name__]
                if e[internal_attr] == attr_val
            )
        )

    def _get_entity_by_attr_swap(
        self, attr: str, entity_type: type[SGEntity], attr_val: str | int
    ) -> SGEntity:
        return self.get_entity_by_attr(entity_type, attr, attr_val)

    def get_entity_by_stub(
        self, entity_type: type[SGEntity], stub: SGEntityStub
    ) -> SGEntity:
        return self.get_entity_by_attr(entity_type, "id", stub.id)

    def get_entities_by_stub(
        self, entity_type: type[SGEntity], stubs: Iterable[SGEntityStub]
    ) -> list[SGEntity]:
        ids = [s.id for s in stubs]
        return [
            entity_type.from_sg(e)
            for e in self._sg_entity_lists[entity_type.__name__]
            if e["id"] in ids
        ]

    @staticmethod
    def _default_entity_attr_mapper(
        entity_list: list[dict], attr: str, **kwargs
    ) -> list[str]:
        return [e[attr] for e in entity_list]

    @staticmethod
    def _filter_asset_list(
        asset_list: list[dict], child_mode: DBInterface.ChildQueryMode
    ) -> list[dict]:
        if child_mode == DBInterface.ChildQueryMode.ALL:
            return asset_list
        if child_mode == DBInterface.ChildQueryMode.CHILDREN:
            return [a for a in asset_list if a["parents"]]
        if child_mode == DBInterface.ChildQueryMode.ROOTS:
            return [a for a in asset_list if not a["parents"]]
        if child_mode == DBInterface.ChildQueryMode.PARENTS:
            return [a for a in asset_list if a["assets"]]
        if child_mode == DBInterface.ChildQueryMode.LEAVES:
            return [a for a in asset_list if not a["assets"]]
        raise IndexError("Not a valid ChildQueryMode", child_mode)

    @staticmethod
    def _asset_attr_mapper(
        asset_list: list[dict],
        attr: str,
        child_mode: DBInterface.ChildQueryMode = DBInterface.ChildQueryMode.LEAVES,
    ) -> list[str]:
        filtered = SGaaDB._filter_asset_list(asset_list, child_mode)
        return [a[attr] for a in filtered]

    _entity_attr_custom_mappers: dict[
        str, Callable[[list[dict], str, Unpack[AttrMappingKwargs]], list[str]]
    ] = {
        Asset.__name__: _asset_attr_mapper.__func__,  # type: ignore[attr-defined]
    }

    def get_entity_attr_list(
        self,
        entity_type: type[SGEntity],
        attr: str,
        *,
        sorted: bool = False,
        **kwargs,
    ) -> list[str]:
        if entity_type is Asset and attr == "path":
            filtered_assets = self._filter_asset_list(
                self._sg_entity_lists[Asset.__name__],
                kwargs.get("child_mode", DBInterface.ChildQueryMode.LEAVES),
            )
            arr = [
                self._canonical_asset_path_from_sg(asset) for asset in filtered_assets
            ]
            if sorted:
                arr.sort()
            return arr

        mapper = self._entity_attr_custom_mappers.get(
            entity_type.__name__, self._default_entity_attr_mapper
        )
        internal_attr = entity_type.map_sg_field_names(attr)
        entity_list = self._sg_entity_lists[entity_type.__name__]
        arr = mapper(entity_list, internal_attr, **kwargs)
        if sorted:
            arr.sort()
        return arr

    def _get_entity_attr_list_swap(
        self,
        attr: str,
        entity_type: type[SGEntity],
        **kwargs,
    ) -> list[str]:
        return self.get_entity_attr_list(entity_type, attr, **kwargs)

    def update_entity(self, entity: SGEntity) -> bool:
        """
        General-purpose updater for any SGEntity subclass.
        Calls sg.update using the entity's type, ID, and computed diff.
        """
        try:
            assert entity.id, "Entity must have a valid ID to be updated"
            entity_type = entity.__class__.__name__  # e.g., 'Asset', 'Shot'
            sg_payload = entity.sg_diff()
            self._sg.update(entity_type, entity.id, sg_payload)
        except Exception as e:
            log.error(f"Failed to update {entity_type} (ID {entity.id}): {e}")
            return False
        finally:
            self.expire_cache()
        return True

    get_entity_code_list: T_GetEntityCodeList = pm(_get_entity_attr_list_swap, "code")  # type: ignore[assignment] # noqa: F405
    get_entity_by_code: T_GetEntityByCode = pm(_get_entity_by_attr_swap, "code")  # type: ignore[assignment] # noqa: F405

    get_asset_attr_list: T_GetAssetAttrList = pm(get_entity_attr_list, Asset)  # type: ignore[assignment] # noqa: F405
    get_asset_by_attr: T_GetAssetByAttr = pm(get_entity_by_attr, Asset)  # type: ignore[assignment] # noqa: F405
    get_asset_by_display_name: T_GetAssetByDisplayName = pm(get_asset_by_attr, "code")  # type: ignore[assignment] # noqa: F405
    get_asset_by_id: T_GetAssetById = pm(get_asset_by_attr, "id")  # type: ignore[assignment] # noqa: F405
    get_asset_by_stub: T_GetAssetByStub = pm(get_entity_by_stub, Asset)  # type: ignore[assignment] # noqa: F405
    get_asset_display_name_list: T_GetAssetDisplayNameList = pm(
        get_asset_attr_list, "code"
    )  # type: ignore[assignment] # noqa: F405
    get_assets_by_stub: T_GetAssetsByStub = pm(get_entities_by_stub, Asset)  # type: ignore[assignment] # noqa: F405

    def get_asset_by_name(self, name: str) -> Asset:
        target = normalize_display_name(name)
        return Asset.from_sg(
            next(
                asset
                for asset in self._sg_entity_lists[Asset.__name__]
                if normalize_display_name(asset.get("code")) == target
            )
        )

    def get_asset_name_list(
        self,
        child_mode: DBInterface.ChildQueryMode = DBInterface.ChildQueryMode.LEAVES,
        sorted: bool = False,
    ) -> list[str]:
        display_names = self.get_asset_display_name_list(
            child_mode=child_mode, sorted=False
        )
        names = [normalize_display_name(display_name) for display_name in display_names]
        if sorted:
            names.sort()
        return names

    def get_assets_by_name(self, names: Iterable[str]) -> list[Asset]:
        targets = {normalize_display_name(name) for name in names}
        return [
            Asset.from_sg(i)
            for i in set(
                [
                    a
                    for a in self._sg_entity_lists[Asset.__name__]
                    if normalize_display_name(a.get("code")) in targets
                ]
            )
        ]

    def get_assets_by_display_name(self, names: Iterable[str]) -> list[Asset]:
        return [
            Asset.from_sg(i)
            for i in set(
                [
                    a
                    for a in self._sg_entity_lists[Asset.__name__]
                    if a["code"] in list(names)
                ]
            )
        ]

    def update_asset(self, asset: Asset) -> bool:
        try:
            assert asset.id
            self._sg.update("Asset", asset.id, asset.sg_diff())
        except Exception as e:
            log.error(e)
            return False
        finally:
            self.expire_cache()
        return True

    def create_version_for_shot(
        self,
        shot: ShotStub,
        code: str,
        user: User,
        task: Task,
        video_path: Optional[str] = None,
        description: Optional[str] = None,
        playlist_id: Optional[int] = None,
    ) -> dict[Any, Any]:
        # Create Version instance
        version = Version(
            id=-1,
            code=code,
            shot=shot,
            video_path=video_path,
            user=user,
            description=description,
            task=task,
        )

        # Push to ShotGrid
        sg_dict = version.to_sg(exclude=["id"])
        sg_dict["project"] = {"type": "Project", "id": self._id}
        sg_dict["playlists"] = [{"type": "Playlist", "id": playlist_id}]
        new_version = self._sg.create("Version", sg_dict)

        # Return structured object
        return new_version

    def upload_version_movie(self, version_id, path_to_file, field="sg_uploaded_movie"):
        display_name = os.path.basename(path_to_file)
        attachment = self._sg.upload(
            entity_type="Version",
            entity_id=version_id,
            path=path_to_file,
            field_name=field,
            display_name=display_name,
        )
        return attachment

    def get_tasks(self, shot: Shot, user: User) -> list[Task]:
        filters = [
            ["entity", "is", {"type": "Shot", "id": shot.id}],
            ["task_assignees", "in", [{"type": "HumanUser", "id": user.id}]],
        ]

        fields = [
            "id",
            "content",
            "step",
            "task_assignees",
            "versions",
            "sg_status_list",
            "due_date",
            "entity",
            "task_type",
        ]

        raw_tasks = self._sg.find("Task", filters, fields)
        print(raw_tasks)
        return [Task.from_sg(task) for task in raw_tasks]

    def get_asset_display_name_list_by_type(
        self, types: list[str], sorted: bool = False
    ) -> list[str]:
        mapper = self._entity_attr_custom_mappers.get(
            Asset.__name__, self._default_entity_attr_mapper
        )
        internal_attr = Asset.map_sg_field_names("code")
        asset_list = self._sg_entity_lists[Asset.__name__]
        filtered_assets = [a for a in asset_list if a.get("sg_asset_type") in types]

        arr = mapper(
            filtered_assets, internal_attr, child_mode=DBInterface.ChildQueryMode.ALL
        )

        if sorted:
            arr.sort()
        return arr

    def get_asset_name_list_by_type(
        self, types: list[str], sorted: bool = False
    ) -> list[str]:
        display_names = self.get_asset_display_name_list_by_type(types, sorted=False)
        names = [normalize_display_name(display_name) for display_name in display_names]
        if sorted:
            names.sort()
        return names

    get_user_attr_list: T_GetAttrList = pm(get_entity_attr_list, User)  # type: ignore[assignment] # noqa: F405
    get_user_by_attr: T_GetUserByAttr = pm(get_entity_by_attr, User)  # type: ignore[assignment] # noqa: F405
    get_user_name_list: T_GetUserNameList = pm(get_user_attr_list, "name")  # type: ignore[assignment] # noqa: F405
    get_user_by_name: T_GetUserByName = pm(get_user_by_attr, "name")  # type: ignore[assignment] # noqa: F405

    get_env_attr_list: T_GetAttrList = pm(get_entity_attr_list, Environment)  # type: ignore[assignment] # noqa: F405
    get_env_by_attr: T_GetEnvByAttr = pm(get_entity_by_attr, Environment)  # type: ignore[assignment] # noqa: F405
    get_env_by_code: T_GetEnvByCode = pm(get_env_by_attr, "code")  # type: ignore[assignment] # noqa: F405
    get_env_by_id: T_GetEnvById = pm(get_env_by_attr, "id")  # type: ignore[assignment] # noqa: F405
    get_env_by_stub: T_GetEnvByStub = pm(get_entity_by_stub, Environment)  # type: ignore[assignment] # noqa: F405
    get_env_code_list: T_GetCodeList = pm(get_env_attr_list, "code")  # type: ignore[assignment] # noqa: F405
    get_envs_by_stub: T_GetEnvsByStub = pm(get_entities_by_stub, Environment)  # type: ignore[assignment] # noqa: F405

    get_sequence_attr_list: T_GetAttrList = pm(get_entity_attr_list, Sequence)  # type: ignore[assignment] # noqa: F405
    get_sequence_by_attr: T_GetSeqByAttr = pm(get_entity_by_attr, Sequence)  # type: ignore[assignment] # noqa: F405
    get_sequence_by_code: T_GetSeqByCode = pm(get_sequence_by_attr, "code")  # type: ignore[assignment] # noqa: F405
    get_sequence_by_id: T_GetSeqById = pm(get_sequence_by_attr, "id")  # type: ignore[assignment] # noqa: F405
    get_sequence_by_stub: T_GetSeqByStub = pm(get_entity_by_stub, Sequence)  # type: ignore[assignment] # noqa: F405
    get_sequence_code_list: T_GetCodeList = pm(get_sequence_attr_list, "code")  # type: ignore[assignment] # noqa: F405
    get_sequences_by_stub: T_GetSeqsByStub = pm(get_entities_by_stub, Sequence)  # type: ignore[assignment] # noqa: F405

    get_shot_attr_list: T_GetAttrList = pm(get_entity_attr_list, Shot)  # type: ignore[assignment] # noqa: F405
    get_shot_by_attr: T_GetShotByAttr = pm(get_entity_by_attr, Shot)  # type: ignore[assignment] # noqa: F405
    get_shot_by_code: T_GetShotByCode = pm(get_shot_by_attr, "code")  # type: ignore[assignment] # noqa: F405
    get_shot_by_id: T_GetShotById = pm(get_shot_by_attr, "id")  # type: ignore[assignment] # noqa: F405
    get_shot_by_stub: T_GetShotByStub = pm(get_entity_by_stub, Shot)  # type: ignore[assignment] # noqa: F405
    get_shot_code_list: T_GetCodeList = pm(get_shot_attr_list, "code")  # type: ignore[assignment] # noqa: F405
    get_shots_by_stub: T_GetShotsByStub = pm(get_entities_by_stub, Shot)  # type: ignore[assignment] # noqa: F405


class _Query(ABC):
    """Helper class for making queries to a SG connection instance"""

    project_id: int
    fields: list[str]
    filters: list[Filter]

    def __init__(
        self,
        project_id: int,
        *,
        extra_fields: typing.Sequence[str] | None = None,
        override_default_fields: bool = False,
    ) -> None:
        if extra_fields is None:
            extra_fields = []
        self.project_id = project_id
        self.fields = self._construct_fields(extra_fields, override_default_fields)
        self.filters = self._construct_filters()

    def _construct_fields(
        self, extra_fields: typing.Sequence[str], override_default_fields: bool
    ) -> list[str]:
        """Construct the fields needed for the ShotGrid query"""
        if override_default_fields:
            return list(extra_fields)
        else:
            return list(set(self._base_fields + list(extra_fields)))

    def _construct_filters(self) -> list[Filter]:
        """Construct the list of filters needed for the ShotGrid query"""
        base_filters = self._base_filters
        base_filters.insert(
            0, ("project", "is", {"type": "Project", "id": self.project_id})
        )
        return base_filters

    def insert_field(self, field: str) -> None:
        self.fields.append(field)

    def insert_filter(self, filter: Filter) -> None:
        self.filters.append(filter)

    @abstractmethod
    def exec(self, sg: shotgun_api3.Shotgun) -> Any:
        pass

    @property
    @abstractmethod
    def _base_fields(self) -> list[str]:
        pass

    @property
    @abstractmethod
    def _base_filters(self) -> list[Filter]:
        pass


class _AssetListQuery(_Query):
    """Helper class for making queries about assets to a SG connection instance"""

    _untracked_asset_types = [
        "Environment",
        "FX",
        "Graphic",
        "Matte Painting",
        "Vehicle",
        "Tool",
        "Font",
    ]

    # Override
    def exec(self, sg: shotgun_api3.Shotgun) -> list[dict]:
        return sg.find("Asset", self.filters, self.fields)

    # Override
    @property
    def _base_fields(self) -> list[str]:
        return [
            "code",  # display name
            "sg_subdirectory",  # asset grouping folder (single level)
            "sg_path",  # legacy asset path (compatibility fallback only)
            "id",  # asset id
            "parents",  # parent assets
            "assets",  # child assets
            "tags",  # asset tags
            "shots",  # shots asset present in
            "sg_material_variants",  # material variants
            "sg_geometry_variants",  # geometry variants
            "sg_material_layers",  # material layers for layered materials
            "sg_asset_type",  # asset type in shotgrid
        ]

    # Override
    @property
    def _base_filters(self) -> list[Filter]:
        filters: list[Filter] = [
            ("sg_status_list", "is_not", "oop"),
            {
                "filter_operator": "all",
                "filters": [
                    ("sg_asset_type", "is_not", t) for t in self._untracked_asset_types
                ],
            },
        ]

        return filters


class _UserListQuery(_Query):
    """Helper class for making queries about users to a SG connection instance"""

    # Override
    def exec(self, sg: shotgun_api3.Shotgun) -> list[dict]:
        return sg.find("HumanUser", self.filters, self.fields)

    # Override
    @property
    def _base_fields(self) -> list[str]:
        return [
            "id",  # user id
            "name",  # User's name
            "login",  # email
        ]

    # Override
    @property
    def _base_filters(self) -> list[Filter]:
        filters: list[Filter] = [("sg_status_list", "is_not", "dis")]
        return filters

    # Override
    def _construct_filters(self) -> list[Filter]:
        """Construct the list of filters needed for the ShotGrid query"""
        base_filters = self._base_filters
        return base_filters


class _EnvironmentListQuery(_Query):
    # Override
    def exec(self, sg: shotgun_api3.Shotgun) -> list[dict]:
        return sg.find("Asset", self.filters, self.fields)

    # Override
    @property
    def _base_fields(self) -> list[str]:
        return [
            "code",  # display name
            "sg_path",  # environment path
            "id",  # asset id
            "shots",  # shots environment present in
        ]

    # Override
    @property
    def _base_filters(self) -> list[Filter]:
        filters: list[Filter] = [
            ("sg_status_list", "is_not", "oop"),
            ("sg_asset_type", "is", "Environment"),
        ]

        return filters


class _ShotListQuery(_Query):
    """Helper class for making queries about shots to a SG connection instance"""

    # Override
    def exec(self, sg: shotgun_api3.Shotgun) -> list[dict]:
        return sg.find("Shot", self.filters, self.fields)

    # Override
    @property
    def _base_fields(self) -> list[str]:
        return [
            "assets",
            "code",
            "id",
            "sg_cut_in",
            "sg_cut_out",
            "sg_cut_duration",
            "sg_sequence",
            "sg_set",
            "sg_sets",
        ]

    # Override
    @property
    def _base_filters(self) -> list[Filter]:
        filters: list[Filter] = [("sg_status_list", "is_not", "oop")]

        return filters


class _SequenceListQuery(_Query):
    """Helper class for making queries about sequences to a SG connection instance"""

    # Override
    def exec(self, sg: shotgun_api3.Shotgun) -> list[dict]:
        return sg.find("Sequence", self.filters, self.fields)

    # Override
    @property
    def _base_fields(self) -> list[str]:
        return [
            "code",
            "id",
            "sg_path",
            "sg_set",
            "sg_sets",
            "shots",
        ]

    # Override
    @property
    def _base_filters(self) -> list[Filter]:
        filters: list[Filter] = [("sg_status_list", "is_not", "oop")]

        return filters
