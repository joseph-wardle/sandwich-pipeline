from __future__ import annotations

import logging
import os
import threading
import urllib.request
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from functools import partialmethod as pm
from typing import Any, Callable, Iterable, Optional, Unpack
from typing import Sequence as SequenceT

import shotgun_api3

from pipe.struct.db import (
    Asset,
    AssetStub,
    Environment,
    Sequence,
    SGEntity,
    SGEntityStub,
    Shot,
    ShotStub,
    Task,
    User,
    build_asset_path,
    build_shot_path,
    normalize_display_name,
)

from .interface import DBInterface
from .typing import (
    AttrMappingKwargs,
    Filter,
    T_GetAssetAttrList,
    T_GetAssetByAttr,
    T_GetAssetByDisplayName,
    T_GetAssetById,
    T_GetAssetByStub,
    T_GetAssetDisplayNameList,
    T_GetAssetsByStub,
    T_GetAttrList,
    T_GetCodeList,
    T_GetEntityByCode,
    T_GetEntityCodeList,
    T_GetEnvByAttr,
    T_GetEnvByCode,
    T_GetEnvById,
    T_GetEnvByStub,
    T_GetEnvsByStub,
    T_GetSeqByAttr,
    T_GetSeqByCode,
    T_GetSeqById,
    T_GetSeqByStub,
    T_GetSeqsByStub,
    T_GetShotByAttr,
    T_GetShotByCode,
    T_GetShotById,
    T_GetShotByStub,
    T_GetShotsByStub,
    T_GetUserByAttr,
    T_GetUserByName,
    T_GetUserNameList,
)

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
        self._apply_houdini_shotgrid_upload_runtime_patch()
        ca_certs_path = self._resolve_shotgrid_ca_bundle_for_current_dcc()
        if ca_certs_path:
            log.info("ShotGrid CA bundle selected: %s", ca_certs_path)
        self._sg = shotgun_api3.Shotgun(
            config.sg_server,
            config.sg_script,
            config.sg_key,
            ca_certs=ca_certs_path,
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

    @staticmethod
    def _is_houdini_runtime() -> bool:
        return os.environ.get("DCC", "").strip().lower() == "houdini"

    @classmethod
    def _apply_houdini_shotgrid_upload_runtime_patch(cls) -> None:
        """Apply a Houdini-only runtime fix for ShotGrid API upload HTTPS classes.

        We intentionally keep the vendored `shotgun_api3` source unmodified and
        patch the legacy upload HTTPS classes at runtime from our DB wrapper.
        """
        if not cls._is_houdini_runtime():
            return

        from shotgun_api3 import shotgun as shotgun_module

        if getattr(shotgun_module, "_PIPELINE_HOUDINI_UPLOAD_PATCHED", False):
            return

        import http.client
        import ssl

        class _PipelineCACertsHTTPSConnection(http.client.HTTPConnection):
            """Drop-in replacement with Python 3.11-compatible super() usage."""

            default_port = http.client.HTTPS_PORT

            def __init__(self, *args, **kwargs):
                self.__ca_certs = kwargs.pop("ca_certs")
                super().__init__(*args, **kwargs)

            def connect(self):
                super().connect()
                if shotgun_module.six.PY38:
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    context.verify_mode = ssl.CERT_REQUIRED
                    context.check_hostname = False
                    if self.__ca_certs:
                        context.load_verify_locations(self.__ca_certs)
                    self.sock = context.wrap_socket(self.sock)
                else:
                    self.sock = ssl.wrap_socket(  # type: ignore
                        self.sock,
                        ca_certs=self.__ca_certs,
                        cert_reqs=ssl.CERT_REQUIRED,
                    )

        class _PipelineCACertsHTTPSHandler(urllib.request.HTTPSHandler):
            """HTTPS opener that routes upload requests through custom CA certs."""

            def __init__(self, ca_certs):
                super().__init__()
                self.__ca_certs = ca_certs

            def https_open(self, req):
                return self.do_open(self.create_https_connection, req)

            def create_https_connection(self, *args, **kwargs):
                return _PipelineCACertsHTTPSConnection(
                    *args,
                    ca_certs=self.__ca_certs,
                    **kwargs,
                )

        setattr(
            shotgun_module,
            "CACertsHTTPSConnection",
            _PipelineCACertsHTTPSConnection,
        )
        setattr(
            shotgun_module,
            "CACertsHTTPSHandler",
            _PipelineCACertsHTTPSHandler,
        )
        setattr(shotgun_module, "_PIPELINE_HOUDINI_UPLOAD_PATCHED", True)
        log.info("Applied Houdini ShotGrid upload HTTPS runtime compatibility patch.")

    @staticmethod
    def _resolve_shotgrid_ca_bundle_for_current_dcc() -> str | None:
        """Resolve CA bundle path for DCCs that need explicit TLS trust config."""
        if not SGaaDB._is_houdini_runtime():
            return None

        explicit_path = os.environ.get("SHOTGUN_API_CACERTS", "").strip()
        if explicit_path:
            if os.path.isfile(explicit_path):
                return explicit_path
            log.warning(
                "SHOTGUN_API_CACERTS is set but file does not exist: %s",
                explicit_path,
            )

        system_ca_bundle_candidates = (
            "/etc/ssl/certs/ca-certificates.crt",
            "/etc/pki/tls/certs/ca-bundle.crt",
            "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
            "/etc/ssl/cert.pem",
            "/etc/ssl/certs/ca-bundle.crt",
        )
        for candidate_path in system_ca_bundle_candidates:
            if os.path.isfile(candidate_path):
                return candidate_path

        log.warning(
            "No system CA bundle candidate was found for Houdini ShotGrid uploads."
        )
        return None

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

    @staticmethod
    def _canonical_shot_path_from_sg(shot: dict) -> str:
        return build_shot_path(shot.get("code"))

    def _shot_matches_path(self, shot: dict, target_path: str) -> bool:
        try:
            canonical = self._normalize_relative_path(
                self._canonical_shot_path_from_sg(shot)
            )
        except ValueError as exc:
            log.error(
                "Invalid shot code in ShotGrid (id=%s code=%r): %s",
                shot.get("id"),
                shot.get("code"),
                exc,
            )
            return False
        return canonical == target_path

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
        if entity_type is Shot and attr == "path":
            target = self._normalize_relative_path(str(attr_val))
            return Shot.from_sg(
                next(
                    e
                    for e in self._sg_entity_lists[Shot.__name__]
                    if self._shot_matches_path(e, target)
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
        Asset.__name__: _asset_attr_mapper.__func__,  # type: ignore
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
        if entity_type is Shot and attr == "path":
            shot_paths: list[str] = []
            for shot in self._sg_entity_lists[Shot.__name__]:
                if not shot.get("code"):
                    continue
                try:
                    shot_paths.append(self._canonical_shot_path_from_sg(shot))
                except ValueError as exc:
                    log.error(
                        "Invalid shot code in ShotGrid (id=%s code=%r): %s",
                        shot.get("id"),
                        shot.get("code"),
                        exc,
                    )
            if sorted:
                shot_paths.sort()
            return shot_paths

        mapper = self._entity_attr_custom_mappers.get(
            entity_type.__name__, self._default_entity_attr_mapper
        )
        internal_attr = entity_type.map_sg_field_names(attr)
        entity_list = self._sg_entity_lists[entity_type.__name__]
        values = mapper(entity_list, internal_attr, **kwargs)
        if sorted:
            values.sort()
        return values

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

    get_entity_code_list: T_GetEntityCodeList = pm(_get_entity_attr_list_swap, "code")  # type: ignore # noqa: F405
    get_entity_by_code: T_GetEntityByCode = pm(_get_entity_by_attr_swap, "code")  # type: ignore # noqa: F405

    get_asset_attr_list: T_GetAssetAttrList = pm(get_entity_attr_list, Asset)  # type: ignore # noqa: F405
    get_asset_by_attr: T_GetAssetByAttr = pm(get_entity_by_attr, Asset)  # type: ignore # noqa: F405
    get_asset_by_display_name: T_GetAssetByDisplayName = pm(get_asset_by_attr, "code")  # type: ignore # noqa: F405
    get_asset_by_id: T_GetAssetById = pm(get_asset_by_attr, "id")  # type: ignore # noqa: F405
    get_asset_by_stub: T_GetAssetByStub = pm(get_entity_by_stub, Asset)  # type: ignore # noqa: F405
    get_asset_display_name_list: T_GetAssetDisplayNameList = pm(  # noqa: F405
        get_asset_attr_list, "code"
    )  # type: ignore # noqa: F405
    get_assets_by_stub: T_GetAssetsByStub = pm(get_entities_by_stub, Asset)  # type: ignore # noqa: F405

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

    def get_assets_by_tag(
        self, tags: Iterable[str] | str, require_all_tags: bool = False
    ) -> list[Asset]:
        """
        Gets all assets that have the specified tags.

        Args:
                tags: Set of tags used for filtering.
                require_all_tags: Determines matching behavior.
                    - True: An asset must contain all provided tags.
                    - False: An asset must contain at least one of the provided tags.

        Returns:
            A list of Asset objects that satisfy the tag query.
        """
        tags_set: set[str]
        if isinstance(tags, str):
            tags_set = {tags}
        else:
            tags_set = set(tags)
        matches: list[dict]
        if require_all_tags:
            matches = [
                asset
                for asset in self._sg_entity_lists[Asset.__name__]
                if tags_set <= set(tag["name"] for tag in asset["tags"])
            ]
        else:
            matches = [
                asset
                for asset in self._sg_entity_lists[Asset.__name__]
                if tags_set & set(tag["name"] for tag in asset["tags"])
            ]

        return [Asset.from_sg(dict) for dict in matches]

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
        shot: Shot | ShotStub | dict[str, Any] | int,
        code: str,
        user: User | dict[str, Any] | int | None = None,
        task: Task | dict[str, Any] | int | None = None,
        video_path: Optional[str] = None,
        description: Optional[str] = None,
        playlist_id: Optional[int] = None,
        extra_fields: Optional[dict[str, Any]] = None,
    ) -> dict[Any, Any]:
        """Create a ShotGrid Version linked to a shot.

        `code` and `shot` are required. `user`, `task`, and `playlist_id`
        are optional and only included when valid ids are provided.
        `extra_fields` can provide additional ShotGrid Version fields.
        """

        version_code = str(code).strip()
        if not version_code:
            raise ValueError("Version code is required.")

        shot_ref = self._entity_ref("Shot", shot)
        if shot_ref is None:
            raise ValueError("A valid shot (with id) is required.")

        return self._create_version_for_entity(
            entity_ref=shot_ref,
            code=version_code,
            user=user,
            task=task,
            video_path=video_path,
            description=description,
            playlist_id=playlist_id,
            extra_fields=extra_fields,
        )

    def create_version_for_asset(
        self,
        asset: Asset | AssetStub | dict[str, Any] | int,
        code: str,
        user: User | dict[str, Any] | int | None = None,
        task: Task | dict[str, Any] | int | None = None,
        video_path: Optional[str] = None,
        description: Optional[str] = None,
        playlist_id: Optional[int] = None,
        extra_fields: Optional[dict[str, Any]] = None,
    ) -> dict[Any, Any]:
        """Create a ShotGrid Version linked to an asset.

        `code` and `asset` are required. `user`, `task`, and `playlist_id`
        are optional and only included when valid ids are provided.
        `extra_fields` can provide additional ShotGrid Version fields.
        """

        version_code = str(code).strip()
        if not version_code:
            raise ValueError("Version code is required.")

        asset_ref = self._entity_ref("Asset", asset)
        if asset_ref is None:
            raise ValueError("A valid asset (with id) is required.")

        return self._create_version_for_entity(
            entity_ref=asset_ref,
            code=version_code,
            user=user,
            task=task,
            video_path=video_path,
            description=description,
            playlist_id=playlist_id,
            extra_fields=extra_fields,
        )

    def _create_version_for_entity(
        self,
        *,
        entity_ref: dict[str, str | int],
        code: str,
        user: User | dict[str, Any] | int | None = None,
        task: Task | dict[str, Any] | int | None = None,
        video_path: Optional[str] = None,
        description: Optional[str] = None,
        playlist_id: Optional[int] = None,
        extra_fields: Optional[dict[str, Any]] = None,
    ) -> dict[Any, Any]:
        version_payload: dict[str, Any] = {
            "code": code,
            "entity": entity_ref,
            "project": {"type": "Project", "id": self._id},
        }

        normalized_video_path = str(video_path).strip() if video_path else ""
        if normalized_video_path:
            version_payload["sg_path_to_frames"] = normalized_video_path

        normalized_description = str(description).strip() if description else ""
        if normalized_description:
            version_payload["description"] = normalized_description

        user_ref = self._entity_ref("HumanUser", user)
        if user_ref is not None:
            version_payload["user"] = user_ref

        task_ref = self._entity_ref("Task", task)
        if task_ref is not None:
            version_payload["sg_task"] = task_ref

        playlist_ref = self._entity_ref("Playlist", playlist_id)
        if playlist_ref is not None:
            version_payload["playlists"] = [playlist_ref]

        self._apply_extra_version_fields(version_payload, extra_fields)
        return self._sg.create("Version", version_payload)

    def get_recent_review_playlists(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent active project playlists for review workflows.

        The result rows are intentionally lightweight and only include
        ``id``, ``code``, ``updated_at``, and ``created_at``.
        """

        normalized_limit = self._normalize_query_limit(limit, default_limit=10)

        filters: list[Filter] = [
            ("project", "is", {"type": "Project", "id": self._id}),
        ]
        fields = ["id", "code", "updated_at", "created_at"]
        order = [{"field_name": "updated_at", "direction": "desc"}]

        raw_playlists = self._find_recent_project_playlists(
            base_filters=filters,
            fields=fields,
            order=order,
            limit=normalized_limit,
        )

        normalized_rows: list[dict[str, Any]] = []
        for raw_playlist in raw_playlists:
            row = self._normalize_review_playlist_row(raw_playlist)
            if row is None:
                continue
            normalized_rows.append(row)
        return normalized_rows

    def _find_recent_project_playlists(
        self,
        *,
        base_filters: list[Filter],
        fields: list[str],
        order: list[dict[str, str]],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Query recent project playlists with a schema-safe active filter.

        Some projects expose a Playlist status field, while others do not.
        We first try adding an explicit "not closed" status filter when the
        schema reports a supported status field. If that query fails because
        the field/value is not valid for the current site schema, we retry
        without the status filter so review loading never hard-fails.
        """

        status_filter = self._playlist_active_status_filter()
        filters = list(base_filters)
        if status_filter is not None:
            filters.append(status_filter)

        try:
            return self._sg.find(
                "Playlist",
                filters,
                fields,
                order=order,  # type: ignore[arg-type]
                limit=limit,
                include_archived_projects=False,
            )
        except shotgun_api3.Fault:
            if status_filter is None:
                raise
            log.warning(
                "Playlist active-status filter failed; retrying without status filter.",
                exc_info=True,
            )
            return self._sg.find(
                "Playlist",
                base_filters,
                fields,
                order=order,  # type: ignore[arg-type]
                limit=limit,
                include_archived_projects=False,
            )

    def _playlist_active_status_filter(self) -> Filter | None:
        """Return a best-effort Playlist status filter, if schema supports one."""

        try:
            schema = self._sg.schema_field_read("Playlist")
        except Exception:
            log.debug("Could not read Playlist schema; skipping status filter.")
            return None

        if not isinstance(schema, dict):
            return None

        if "sg_status_list" in schema:
            return ("sg_status_list", "is_not", "clsd")
        if "sg_status" in schema:
            return ("sg_status", "is_not", "clsd")
        return None

    def link_version_to_playlist(
        self,
        version_id: int | dict[str, Any],
        playlist_id: int | dict[str, Any],
    ) -> dict[str, Any]:
        """Link an existing Version to a Playlist without replacing prior links."""

        resolved_version_id = self._extract_entity_id(version_id)
        if resolved_version_id is None:
            raise ValueError("A valid Version id is required.")

        playlist_ref = self._entity_ref("Playlist", playlist_id)
        if playlist_ref is None:
            raise ValueError("A valid Playlist id is required.")

        return self._sg.update(
            "Version",
            resolved_version_id,
            {"playlists": [playlist_ref]},
            multi_entity_update_modes={"playlists": "add"},
        )

    @staticmethod
    def _entity_ref(entity_type: str, entity: Any) -> dict[str, str | int] | None:
        entity_id = SGaaDB._extract_entity_id(entity)
        if entity_id is None:
            return None
        return {"type": entity_type, "id": entity_id}

    @staticmethod
    def _normalize_query_limit(limit: Any, *, default_limit: int) -> int:
        if limit is None:
            return default_limit

        try:
            normalized = int(limit)
        except Exception as exc:
            raise ValueError("Limit must be a positive integer.") from exc

        if normalized < 1:
            raise ValueError("Limit must be a positive integer.")
        return normalized

    @staticmethod
    def _normalize_review_playlist_row(
        raw_playlist: dict[str, Any],
    ) -> dict[str, Any] | None:
        playlist_id = SGaaDB._extract_entity_id(raw_playlist)
        if playlist_id is None:
            return None

        code = str(raw_playlist.get("code") or "").strip()
        return {
            "id": playlist_id,
            "code": code,
            "updated_at": raw_playlist.get("updated_at"),
            "created_at": raw_playlist.get("created_at"),
        }

    @staticmethod
    def _extract_entity_id(entity: Any) -> int | None:
        if entity is None:
            return None

        if isinstance(entity, int):
            return entity if entity > 0 else None

        if isinstance(entity, str):
            token = entity.strip()
            if not token:
                return None
            try:
                parsed = int(token)
            except ValueError:
                return None
            return parsed if parsed > 0 else None

        if isinstance(entity, dict):
            raw_id = entity.get("id")
            return raw_id if isinstance(raw_id, int) and raw_id > 0 else None

        raw_id = getattr(entity, "id", None)
        return raw_id if isinstance(raw_id, int) and raw_id > 0 else None

    def _apply_extra_version_fields(
        self,
        version_payload: dict[str, Any],
        extra_fields: Optional[dict[str, Any]],
    ) -> None:
        if not extra_fields:
            return

        reserved_fields = {"code", "entity", "project"}
        for raw_field_name, value in extra_fields.items():
            field_name = str(raw_field_name).strip()
            if not field_name or value is None:
                continue
            if field_name in reserved_fields:
                log.warning(
                    "Ignoring extra Version field '%s' because it is reserved.",
                    field_name,
                )
                continue
            version_payload[field_name] = value

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
        return [Task.from_sg(task) for task in raw_tasks]

    def get_asset_display_name_list_by_type(
        self, types: list[str], sorted: bool = False
    ) -> list[str]:
        internal_attr = Asset.map_sg_field_names("code")
        asset_list = self._sg_entity_lists[Asset.__name__]
        filtered_assets = [a for a in asset_list if a.get("sg_asset_type") in types]

        arr = self._asset_attr_mapper(
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

    get_user_attr_list: T_GetAttrList = pm(get_entity_attr_list, User)  # type: ignore # noqa: F405
    get_user_by_attr: T_GetUserByAttr = pm(get_entity_by_attr, User)  # type: ignore # noqa: F405
    get_user_name_list: T_GetUserNameList = pm(get_user_attr_list, "name")  # type: ignore # noqa: F405
    get_user_by_name: T_GetUserByName = pm(get_user_by_attr, "name")  # type: ignore # noqa: F405

    get_env_attr_list: T_GetAttrList = pm(get_entity_attr_list, Environment)  # type: ignore # noqa: F405
    get_env_by_attr: T_GetEnvByAttr = pm(get_entity_by_attr, Environment)  # type: ignore # noqa: F405
    get_env_by_code: T_GetEnvByCode = pm(get_env_by_attr, "code")  # type: ignore # noqa: F405
    get_env_by_id: T_GetEnvById = pm(get_env_by_attr, "id")  # type: ignore # noqa: F405
    get_env_by_stub: T_GetEnvByStub = pm(get_entity_by_stub, Environment)  # type: ignore # noqa: F405
    get_env_code_list: T_GetCodeList = pm(get_env_attr_list, "code")  # type: ignore # noqa: F405
    get_envs_by_stub: T_GetEnvsByStub = pm(get_entities_by_stub, Environment)  # type: ignore # noqa: F405

    get_sequence_attr_list: T_GetAttrList = pm(get_entity_attr_list, Sequence)  # type: ignore # noqa: F405
    get_sequence_by_attr: T_GetSeqByAttr = pm(get_entity_by_attr, Sequence)  # type: ignore # noqa: F405
    get_sequence_by_code: T_GetSeqByCode = pm(get_sequence_by_attr, "code")  # type: ignore # noqa: F405
    get_sequence_by_id: T_GetSeqById = pm(get_sequence_by_attr, "id")  # type: ignore # noqa: F405
    get_sequence_by_stub: T_GetSeqByStub = pm(get_entity_by_stub, Sequence)  # type: ignore # noqa: F405
    get_sequence_code_list: T_GetCodeList = pm(get_sequence_attr_list, "code")  # type: ignore # noqa: F405
    get_sequences_by_stub: T_GetSeqsByStub = pm(get_entities_by_stub, Sequence)  # type: ignore # noqa: F405

    get_shot_attr_list: T_GetAttrList = pm(get_entity_attr_list, Shot)  # type: ignore # noqa: F405
    get_shot_by_attr: T_GetShotByAttr = pm(get_entity_by_attr, Shot)  # type: ignore # noqa: F405
    get_shot_by_code: T_GetShotByCode = pm(get_shot_by_attr, "code")  # type: ignore # noqa: F405
    get_shot_by_id: T_GetShotById = pm(get_shot_by_attr, "id")  # type: ignore # noqa: F405
    get_shot_by_stub: T_GetShotByStub = pm(get_entity_by_stub, Shot)  # type: ignore # noqa: F405
    get_shot_code_list: T_GetCodeList = pm(get_shot_attr_list, "code")  # type: ignore # noqa: F405
    get_shots_by_stub: T_GetShotsByStub = pm(get_entities_by_stub, Shot)  # type: ignore # noqa: F405


class _Query(ABC):
    """Helper class for making queries to a SG connection instance"""

    project_id: int
    fields: list[str]
    filters: list[Filter]

    def __init__(
        self,
        project_id: int,
        *,
        extra_fields: SequenceT[str] | None = None,
        override_default_fields: bool = False,
    ) -> None:
        if extra_fields is None:
            extra_fields = []
        self.project_id = project_id
        self.fields = self._construct_fields(extra_fields, override_default_fields)
        self.filters = self._construct_filters()

    def _construct_fields(
        self, extra_fields: SequenceT[str], override_default_fields: bool
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
            "sg_subdirectory",  # optional subdirectory for path derivation
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
