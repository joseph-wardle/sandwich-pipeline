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
    build_shot_path,
    normalize_display_name,
)

if TYPE_CHECKING:
    import typing
    from typing import Any, Callable, Iterable, Optional

    from .typing import *  # noqa: F403
    from .typing import Filter

from . import shotgun_api3
from .interface import DBInterface

log = logging.getLogger(__name__)


@dataclass(eq=True, frozen=True)
class SGConfig:
    project_id: int
    # DO NOT SHARE/COMMIT THE sg_key!!! IT'S EQUIVALENT TO AN ADMIN PW!!!
    sg_key: str
    sg_script: str
    sg_server: str


# Backward-compatible name used by existing callers.
SG_Config = SGConfig


class SGaaDBError(RuntimeError):
    """Base class for ShotGrid DB domain errors."""


class EntityNotFound(SGaaDBError):
    """Raised when an entity lookup returns no result."""


class InvalidQueryAttr(SGaaDBError):
    """Raised when a query uses an unsupported or invalid attribute."""


class CacheNotReady(SGaaDBError):
    """Raised when cached data for an entity type is unavailable."""


class ShotGridClient:
    """Thin wrapper around the ShotGrid API client.

    This class intentionally contains only raw I/O operations so higher-level
    logic can be reasoned about separately.
    """

    _sg: shotgun_api3.Shotgun
    _project_id: int

    def __init__(self, config: SGConfig) -> None:
        self._sg = shotgun_api3.Shotgun(
            config.sg_server, config.sg_script, config.sg_key
        )
        self._project_id = config.project_id

    @property
    def project_id(self) -> int:
        """Return the ShotGrid project id used for all queries."""
        return self._project_id

    def run_query(self, query: Any) -> list[dict]:
        """Execute a query helper object and return raw ShotGrid rows."""
        return query.exec(self._sg)

    def find(self, entity_type: str, filters: list, fields: list[str]) -> list[dict]:
        """Proxy to ``Shotgun.find`` for direct entity queries."""
        return self._sg.find(entity_type, filters, fields)

    def update(self, entity_type: str, entity_id: int, payload: dict[str, Any]) -> None:
        """Update a single ShotGrid entity with the provided payload."""
        self._sg.update(entity_type, entity_id, payload)

    def create(self, entity_type: str, payload: dict[str, Any]) -> dict[Any, Any]:
        """Create a ShotGrid entity and return the created row."""
        return self._sg.create(entity_type, payload)

    def upload(
        self,
        *,
        entity_type: str,
        entity_id: int,
        path: str,
        field_name: str,
        display_name: str,
    ) -> Any:
        """Upload a file to an upload-capable ShotGrid entity field."""
        return self._sg.upload(
            entity_type=entity_type,
            entity_id=entity_id,
            path=path,
            field_name=field_name,
            display_name=display_name,
        )


class EntityCache:
    """Thread-safe in-memory cache for ShotGrid entity lists.

    Cache refresh modes:
    - `refresh_now(...)`: synchronous refresh.
    - `expire()`: asynchronous refresh signal for the background thread.
    """

    _loaders: dict[str, Callable[[], list[dict]]]
    _refresh_targets: tuple[str, ...]
    _refresh_interval_seconds: int
    _data: dict[str, list[dict]]
    _lock: threading.Lock
    _notifier: threading.Condition
    _thread: threading.Thread

    def __init__(
        self,
        *,
        loaders: dict[str, Callable[[], list[dict]]],
        refresh_targets: tuple[str, ...],
        refresh_interval_seconds: int = 300,
    ) -> None:
        self._loaders = loaders
        self._refresh_targets = refresh_targets
        self._refresh_interval_seconds = refresh_interval_seconds
        self._data = {}
        self._lock = threading.Lock()
        self._notifier = threading.Condition()
        self._thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._thread.start()

    def get(self, entity_name: str) -> list[dict]:
        """Return cached rows for ``entity_name`` or raise ``CacheNotReady``."""
        with self._lock:
            if entity_name not in self._data:
                raise CacheNotReady(
                    f"Cache is not ready for entity type '{entity_name}'"
                )
            return self._data[entity_name]

    def refresh_now(self, entity_names: Iterable[str] | None = None) -> None:
        """Synchronously refresh one or more entity caches from ShotGrid."""
        names = (
            tuple(entity_names) if entity_names is not None else tuple(self._loaders)
        )
        for entity_name in names:
            loader = self._loaders.get(entity_name)
            if loader is None:
                raise InvalidQueryAttr(
                    f"No cache loader registered for entity type '{entity_name}'"
                )
            rows = loader()
            with self._lock:
                self._data[entity_name] = rows

    def expire(self) -> None:
        """Signal the background refresher to update cached refresh targets."""
        with self._notifier:
            self._notifier.notify()

    def _refresh_loop(self) -> None:
        while True:
            with self._notifier:
                self._notifier.wait(timeout=self._refresh_interval_seconds)
            try:
                log.debug("Cache expired, refreshing list")
                self.refresh_now(self._refresh_targets)
            except Exception:
                log.exception("Asynchronous cache refresh failed")


class EntityRepository:
    """Read/write entity behavior over cached ShotGrid data."""

    _cache: EntityCache
    _client: ShotGridClient
    _attr_list_mappers: dict[str, Callable[[list[dict], str, Any], list[str]]]
    _derived_lookup_resolvers: dict[
        type[SGEntity], dict[str, Callable[[str | int], SGEntity]]
    ]
    _derived_list_resolvers: dict[type[SGEntity], dict[str, Callable[..., list[str]]]]

    def __init__(self, *, cache: EntityCache, client: ShotGridClient) -> None:
        self._cache = cache
        self._client = client
        self._attr_list_mappers = {
            Asset.__name__: self._asset_attr_mapper,
        }
        self._derived_lookup_resolvers = {
            Asset: {"path": self._get_asset_by_path},
            Shot: {"path": self._get_shot_by_path},
        }
        self._derived_list_resolvers = {
            Asset: {"path": self._get_asset_path_list},
            Shot: {"path": self._get_shot_path_list},
        }

    def _entity_rows(self, entity_type: type[SGEntity]) -> list[dict]:
        return self._cache.get(entity_type.__name__)

    def log_asset_name_collisions(self) -> None:
        """Log assets whose normalized names collide after canonicalization."""
        asset_list = self._entity_rows(Asset)
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

    @staticmethod
    def _canonical_shot_path_from_sg(shot: dict) -> str:
        return build_shot_path(shot.get("code"))

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

    def _get_asset_by_path(self, attr_val: str | int) -> SGEntity:
        target = self._normalize_relative_path(str(attr_val))
        rows = self._entity_rows(Asset)
        try:
            row = next(
                asset for asset in rows if self._asset_matches_path(asset, target)
            )
        except StopIteration as exc:
            raise EntityNotFound(f"Asset with path '{target}' was not found") from exc
        return Asset.from_sg(row)

    def _get_shot_by_path(self, attr_val: str | int) -> SGEntity:
        target = self._normalize_relative_path(str(attr_val))
        rows = self._entity_rows(Shot)
        try:
            row = next(shot for shot in rows if self._shot_matches_path(shot, target))
        except StopIteration as exc:
            raise EntityNotFound(f"Shot with path '{target}' was not found") from exc
        return Shot.from_sg(row)

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
        raise InvalidQueryAttr(f"Not a valid ChildQueryMode: {child_mode}")

    @staticmethod
    def _default_entity_attr_mapper(
        entity_list: list[dict], attr: str, **kwargs: Any
    ) -> list[str]:
        return [e[attr] for e in entity_list]

    def _asset_attr_mapper(
        self,
        asset_list: list[dict],
        attr: str,
        child_mode: DBInterface.ChildQueryMode = DBInterface.ChildQueryMode.LEAVES,
        **kwargs: Any,
    ) -> list[str]:
        del kwargs
        filtered = self._filter_asset_list(asset_list, child_mode)
        return [a[attr] for a in filtered]

    def _get_asset_path_list(
        self,
        *,
        sorted: bool = False,
        child_mode: DBInterface.ChildQueryMode = DBInterface.ChildQueryMode.LEAVES,
        **kwargs: Any,
    ) -> list[str]:
        del kwargs
        filtered_assets = self._filter_asset_list(self._entity_rows(Asset), child_mode)
        arr = [self._canonical_asset_path_from_sg(asset) for asset in filtered_assets]
        if sorted:
            arr.sort()
        return arr

    def _get_shot_path_list(self, *, sorted: bool = False, **kwargs: Any) -> list[str]:
        del kwargs
        arr: list[str] = []
        for shot in self._entity_rows(Shot):
            if not shot.get("code"):
                continue
            try:
                arr.append(self._canonical_shot_path_from_sg(shot))
            except ValueError as exc:
                log.error(
                    "Invalid shot code in ShotGrid (id=%s code=%r): %s",
                    shot.get("id"),
                    shot.get("code"),
                    exc,
                )
        if sorted:
            arr.sort()
        return arr

    def get_entity_by_attr(
        self, entity_type: type[SGEntity], attr: str, attr_val: str | int
    ) -> SGEntity:
        """Return one entity matching ``attr == attr_val`` from cached rows.

        Derived attributes (for example ``path`` on Asset/Shot) are resolved via
        the registry before direct ShotGrid field lookup.
        """
        derived_resolver = self._derived_lookup_resolvers.get(entity_type, {}).get(attr)
        if derived_resolver:
            return derived_resolver(attr_val)

        internal_attr = entity_type.map_sg_field_names(attr)
        if not internal_attr:
            raise InvalidQueryAttr(
                f"Attribute '{attr}' is not valid for entity type '{entity_type.__name__}'"
            )

        rows = self._entity_rows(entity_type)
        try:
            row = next(entity for entity in rows if entity[internal_attr] == attr_val)
        except KeyError as exc:
            raise InvalidQueryAttr(
                f"ShotGrid field '{internal_attr}' is unavailable for '{entity_type.__name__}'"
            ) from exc
        except StopIteration as exc:
            raise EntityNotFound(
                f"{entity_type.__name__} with {attr}={attr_val!r} was not found"
            ) from exc

        return entity_type.from_sg(row)

    def get_entity_by_stub(
        self, entity_type: type[SGEntity], stub: SGEntityStub
    ) -> SGEntity:
        """Resolve a typed stub to its full entity using the cached id lookup."""
        return self.get_entity_by_attr(entity_type, "id", stub.id)

    def get_entities_by_stub(
        self, entity_type: type[SGEntity], stubs: Iterable[SGEntityStub]
    ) -> list[SGEntity]:
        """Resolve many stubs to entities in a single cached pass."""
        ids = {stub.id for stub in stubs}
        rows = self._entity_rows(entity_type)
        return [entity_type.from_sg(row) for row in rows if row.get("id") in ids]

    def get_entity_attr_list(
        self,
        entity_type: type[SGEntity],
        attr: str,
        *,
        sorted: bool = False,
        **kwargs: Any,
    ) -> list[str]:
        """Return a list of attribute values for a cached entity type.

        Derived attributes are resolved by registry. Non-derived attributes are
        mapped through each struct's ShotGrid field map.
        """
        derived_resolver = self._derived_list_resolvers.get(entity_type, {}).get(attr)
        if derived_resolver:
            return derived_resolver(sorted=sorted, **kwargs)

        internal_attr = entity_type.map_sg_field_names(attr)
        if not internal_attr:
            raise InvalidQueryAttr(
                f"Attribute '{attr}' is not valid for entity type '{entity_type.__name__}'"
            )

        mapper = self._attr_list_mappers.get(
            entity_type.__name__, self._default_entity_attr_mapper
        )
        rows = self._entity_rows(entity_type)
        try:
            arr = mapper(rows, internal_attr, **kwargs)
        except KeyError as exc:
            raise InvalidQueryAttr(
                f"ShotGrid field '{internal_attr}' is unavailable for '{entity_type.__name__}'"
            ) from exc
        if sorted:
            arr.sort()
        return arr

    def update_entity(self, entity: SGEntity) -> bool:
        """Push ``entity.sg_diff()`` to ShotGrid and return success."""
        entity_type = entity.__class__.__name__
        entity_id = getattr(entity, "id", None)
        if not entity_id:
            log.error("Failed to update %s: entity has no valid id", entity_type)
            return False

        try:
            self._client.update(entity_type, entity_id, entity.sg_diff())
        except Exception as exc:
            log.error("Failed to update %s (id=%s): %s", entity_type, entity_id, exc)
            return False
        return True

    def get_asset_by_name(self, name: str) -> Asset:
        """Return one asset by normalized pipeline name."""
        target = normalize_display_name(name)
        try:
            row = next(
                asset
                for asset in self._entity_rows(Asset)
                if normalize_display_name(asset.get("code")) == target
            )
        except StopIteration as exc:
            raise EntityNotFound(
                f"Asset with normalized name '{target}' was not found"
            ) from exc
        return Asset.from_sg(row)

    def get_asset_name_list(
        self,
        child_mode: DBInterface.ChildQueryMode = DBInterface.ChildQueryMode.LEAVES,
        sorted: bool = False,
    ) -> list[str]:
        """Return normalized asset names, optionally filtered by hierarchy mode."""
        display_names = self.get_entity_attr_list(
            Asset, "code", sorted=False, child_mode=child_mode
        )
        names = [normalize_display_name(display_name) for display_name in display_names]
        if sorted:
            names.sort()
        return names

    def get_assets_by_name(self, names: Iterable[str]) -> list[Asset]:
        """Return assets matching any normalized name from ``names``."""
        targets = {normalize_display_name(name) for name in names}
        rows = self._entity_rows(Asset)
        return [
            Asset.from_sg(asset)
            for asset in {
                a["id"]: a
                for a in rows
                if normalize_display_name(a.get("code")) in targets
            }.values()
        ]

    def get_assets_by_display_name(self, names: Iterable[str]) -> list[Asset]:
        """Return assets matching any ShotGrid display name from ``names``."""
        targets = set(names)
        rows = self._entity_rows(Asset)
        return [
            Asset.from_sg(asset)
            for asset in {a["id"]: a for a in rows if a.get("code") in targets}.values()
        ]

    def get_asset_display_name_list_by_type(
        self, types: list[str], sorted: bool = False
    ) -> list[str]:
        """Return asset display names filtered by ShotGrid asset type."""
        rows = [
            asset
            for asset in self._entity_rows(Asset)
            if asset.get("sg_asset_type") in types
        ]
        arr = self._asset_attr_mapper(
            rows,
            Asset.map_sg_field_names("code"),
            child_mode=DBInterface.ChildQueryMode.ALL,
        )
        if sorted:
            arr.sort()
        return arr

    def get_asset_name_list_by_type(
        self, types: list[str], sorted: bool = False
    ) -> list[str]:
        """Return normalized asset names filtered by ShotGrid asset type."""
        display_names = self.get_asset_display_name_list_by_type(types, sorted=False)
        names = [normalize_display_name(display_name) for display_name in display_names]
        if sorted:
            names.sort()
        return names


class SGaaDB(DBInterface):
    """ShotGrid-backed implementation of :class:`DBInterface`.

    Contract:
    - Entity data is read from an in-memory cache (`_cache`) populated
      from ShotGrid at startup.
    - Cache refresh is asynchronous. `expire_cache()` signals the background
      updater thread; it does not block for fresh data.
    - Some attributes are derived rather than read directly from ShotGrid
      fields. For example, `path` for Asset/Shot is computed from canonical
      naming helpers.
    - Lookup methods raise explicit domain exceptions (`EntityNotFound`,
      `InvalidQueryAttr`, `CacheNotReady`) when requests cannot be satisfied.
    """

    _client: ShotGridClient
    _cache: EntityCache
    _repository: EntityRepository
    _project_id: int

    _conn_instances: dict[SGConfig, SGaaDB] = {}

    @classmethod
    def get(cls, config: SGConfig) -> SGaaDB:
        """Return a shared DB instance for the provided ShotGrid config."""
        if config in cls._conn_instances:
            return cls._conn_instances[config]
        else:
            log.debug("Creating new DB instance.")
            cls._conn_instances[config] = cls(config)
            return cls._conn_instances[config]

    @classmethod
    def Get(cls, config: SGConfig) -> SGaaDB:
        """Backward-compatible alias for :meth:`get`."""
        return cls.get(config)

    def __init__(self, config: SGConfig) -> None:
        self._client = ShotGridClient(config)
        self._project_id = self._client.project_id
        self._cache = EntityCache(
            loaders={
                Asset.__name__: lambda: self._client.run_query(
                    _AssetListQuery(self._project_id)
                ),
                User.__name__: lambda: self._client.run_query(
                    _UserListQuery(self._project_id)
                ),
                Environment.__name__: lambda: self._client.run_query(
                    _EnvironmentListQuery(self._project_id)
                ),
                Sequence.__name__: lambda: self._client.run_query(
                    _SequenceListQuery(self._project_id)
                ),
                Shot.__name__: lambda: self._client.run_query(
                    _ShotListQuery(self._project_id)
                ),
            },
            refresh_targets=(Asset.__name__, Shot.__name__),
        )
        self._repository = EntityRepository(cache=self._cache, client=self._client)
        self.refresh_now()
        self._repository.log_asset_name_collisions()

    def refresh_now(self, entity_types: Iterable[type[SGEntity]] | None = None) -> None:
        """Synchronously refresh cached entity lists from ShotGrid."""
        if entity_types is None:
            self._cache.refresh_now()
            return
        self._cache.refresh_now(entity_type.__name__ for entity_type in entity_types)

    def expire_cache(self) -> None:
        """Schedule an asynchronous cache refresh.

        This method only notifies the cache background thread. It does not
        wait for network I/O or guarantee that subsequent reads are fresh.
        """
        self._cache.expire()

    def get_entity_by_attr(
        self, entity_type: type[SGEntity], attr: str, attr_val: str | int
    ) -> SGEntity:
        """Return the first cached entity matching ``attr == attr_val``.

        Notes:
        - ``attr`` is interpreted as a DB/struct attribute name, not a raw
          ShotGrid field name.
        - ``path`` for Asset and Shot is derived from canonical naming rules,
          then matched against ``attr_val`` after path normalization.
        """
        return self._repository.get_entity_by_attr(entity_type, attr, attr_val)

    def _get_entity_by_attr_swap(
        self, attr: str, entity_type: type[SGEntity], attr_val: str | int
    ) -> SGEntity:
        return self.get_entity_by_attr(entity_type, attr, attr_val)

    def get_entity_by_stub(
        self, entity_type: type[SGEntity], stub: SGEntityStub
    ) -> SGEntity:
        """Resolve a typed stub to its full cached entity."""
        return self._repository.get_entity_by_stub(entity_type, stub)

    def get_entities_by_stub(
        self, entity_type: type[SGEntity], stubs: Iterable[SGEntityStub]
    ) -> list[SGEntity]:
        """Resolve multiple typed stubs to full cached entities."""
        return self._repository.get_entities_by_stub(entity_type, stubs)

    def get_entity_attr_list(
        self,
        entity_type: type[SGEntity],
        attr: str,
        *,
        sorted: bool = False,
        **kwargs,
    ) -> list[str]:
        """Return a cached list of attribute values for an entity type.

        ``attr`` uses DB/struct attribute names. Derived attributes are handled
        explicitly:
        - Asset ``path`` is computed from canonical asset naming.
        - Shot ``path`` is computed from canonical shot naming.
        """
        return self._repository.get_entity_attr_list(
            entity_type,
            attr,
            sorted=sorted,
            **kwargs,
        )

    def _get_entity_attr_list_swap(
        self,
        attr: str,
        entity_type: type[SGEntity],
        **kwargs,
    ) -> list[str]:
        return self.get_entity_attr_list(entity_type, attr, **kwargs)

    def update_entity(self, entity: SGEntity) -> bool:
        """Update an entity in ShotGrid and signal asynchronous cache refresh."""
        updated = self._repository.update_entity(entity)
        self.expire_cache()
        return updated

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
        """Return one asset by normalized pipeline name."""
        return self._repository.get_asset_by_name(name)

    def get_asset_name_list(
        self,
        child_mode: DBInterface.ChildQueryMode = DBInterface.ChildQueryMode.LEAVES,
        sorted: bool = False,
    ) -> list[str]:
        """Return normalized asset names with optional hierarchy filtering."""
        return self._repository.get_asset_name_list(
            child_mode=child_mode,
            sorted=sorted,
        )

    def get_assets_by_name(self, names: Iterable[str]) -> list[Asset]:
        """Return all assets matching any normalized name in ``names``."""
        return self._repository.get_assets_by_name(names)

    def get_assets_by_display_name(self, names: Iterable[str]) -> list[Asset]:
        """Return all assets matching any ShotGrid display name in ``names``."""
        return self._repository.get_assets_by_display_name(names)

    def update_asset(self, asset: Asset) -> bool:
        """Backward-compatible asset-specific wrapper around ``update_entity``."""
        return self.update_entity(asset)

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
        """Create and return a ShotGrid ``Version`` row for a shot."""
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
        sg_dict["project"] = {"type": "Project", "id": self._project_id}
        if playlist_id is not None:
            sg_dict["playlists"] = [{"type": "Playlist", "id": playlist_id}]
        new_version = self._client.create("Version", sg_dict)

        # Return structured object
        return new_version

    def upload_version_movie(self, version_id, path_to_file, field="sg_uploaded_movie"):
        """Upload media for a Version field and return ShotGrid's attachment result."""
        display_name = os.path.basename(path_to_file)
        return self._client.upload(
            entity_type="Version",
            entity_id=version_id,
            path=path_to_file,
            field_name=field,
            display_name=display_name,
        )

    def get_tasks(self, shot: Shot, user: User) -> list[Task]:
        """Return tasks assigned to ``user`` on ``shot``."""
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

        raw_tasks = self._client.find("Task", filters, fields)
        return [Task.from_sg(task) for task in raw_tasks]

    def get_asset_display_name_list_by_type(
        self, types: list[str], sorted: bool = False
    ) -> list[str]:
        """Return ShotGrid asset display names for the requested asset types."""
        return self._repository.get_asset_display_name_list_by_type(
            types,
            sorted=sorted,
        )

    def get_asset_name_list_by_type(
        self, types: list[str], sorted: bool = False
    ) -> list[str]:
        """Return normalized asset names for the requested asset types."""
        return self._repository.get_asset_name_list_by_type(types, sorted=sorted)

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
        """Append one field to the query field list."""
        self.fields.append(field)

    def insert_filter(self, filter: Filter) -> None:
        """Append one filter to the query filter list."""
        self.filters.append(filter)

    @abstractmethod
    def exec(self, sg: shotgun_api3.Shotgun) -> Any:
        """Execute this query against a ShotGrid client."""
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
        """Execute an Asset list query."""
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
        """Execute a HumanUser list query."""
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
        """Execute an Environment Asset list query."""
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
        """Execute a Shot list query."""
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
        """Execute a Sequence list query."""
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
