"""ShotGrid client for `sandwich-pipeline`.

This module is the single entry point for every pipeline interaction with
ShotGrid (a.k.a. ShotGun Studio, a.k.a. Autodesk Flow Production Tracking
a.k.a whatever they changed it to now (please update this comment)).
Raw `shotgun_api3` dicts, filters, and `Fault` exceptions never leave
this file.

Read this file top-to-bottom to understand the full ShotGrid surface the
pipeline uses. Sections, in order:

* Connection and configuration (`SG_Config`, `ShotGrid`).
* Read verbs — `get_*` (single entity) and `find_*` (list of entities).
* Write verbs — first-class, idempotent, return the refreshed entity.
* Version creation, movie upload, playlist linking.
* Internals — singleton cache, Houdini SSL workaround, selector validation.
"""

from __future__ import annotations

import http.client
import logging
import os
import socket
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast, overload, TypeVar

import attrs
import shotgun_api3

from core.shotgrid._memoize import invalidate, ttl_cache
from core.shotgrid.entities import (
    Asset,
    Environment,
    Playlist,
    SGEntity,
    Sequence,
    Shot,
    Task,
    User,
    Version,
)
from core.shotgrid.errors import (
    ShotGridAmbiguous,
    ShotGridError,
    ShotGridNotFound,
    ShotGridWriteError,
)
from core.shotgrid.paths import (
    build_asset_path,
    build_environment_path,
    normalize_display_name,
)

log = logging.getLogger(__name__)

_T = TypeVar("_T")
_E = TypeVar("_E", bound=SGEntity)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(eq=True, frozen=True)
class SG_Config:
    """Credentials and project id needed to open a ShotGrid connection.

    `sg_key` is equivalent to an admin password. Never commit it, never log
    it, never include it in error messages. In production this is loaded from
    the gitignored `env_sg.py` synced by the `post-checkout` git hook.
    """

    project_id: int
    sg_key: str
    sg_script: str
    sg_server: str


# ---------------------------------------------------------------------------
# Module-level constants — field lists and filter fragments.
#
# These are the only ShotGrid field-name strings the pipeline knows about.
# If ShotGrid schema changes, every rename starts here.
# ---------------------------------------------------------------------------


_SG_FIELDS_ASSET: tuple[str, ...] = (
    "id",
    "code",
    "tags",
    "sg_asset_type",
    "sg_subdirectory",
    "sg_material_variants",
    "sg_geometry_variants",
    "sg_material_layers",
    # Internal-only: used to filter roots_only=True. Never surfaced on Asset.
    "parents",
)
_SG_FIELDS_SHOT: tuple[str, ...] = (
    "id",
    "code",
    "assets",
    "sg_cut_in",
    "sg_cut_out",
    "sg_cut_duration",
    "sg_sequence",
    "sg_set",
    "sg_sets",
)
_SG_FIELDS_SEQUENCE: tuple[str, ...] = (
    "id",
    "code",
    "shots",
    "sg_set",
    "sg_sets",
)
_SG_FIELDS_ENVIRONMENT: tuple[str, ...] = ("id", "code", "sg_subdirectory")
_SG_FIELDS_USER: tuple[str, ...] = ("id", "name", "login")
_SG_FIELDS_TASK: tuple[str, ...] = ("id", "content", "entity", "sg_status_list")
_SG_FIELDS_VERSION: tuple[str, ...] = (
    "id",
    "code",
    "entity",
    "sg_task",
    "user",
    "sg_path_to_frames",
    "sg_uploaded_movie",
    "description",
)
_SG_FIELDS_PLAYLIST: tuple[str, ...] = (
    "id",
    "code",
    "sg_status_list",
    "versions",
    "updated_at",
    "created_at",
)

# Active-record filters: skip records that are marked out-of-project / disabled.
_SG_STATUS_ACTIVE_FILTER: tuple[str, str, str] = ("sg_status_list", "is_not", "oop")
_SG_STATUS_ACTIVE_USER_FILTER: tuple[str, str, str] = (
    "sg_status_list",
    "is_not",
    "dis",
)

# Assets with these sg_asset_type values are not "real" assets (environments
# are their own entity surface, the others are legacy). Used by find_assets.
_SG_ASSET_TYPE_EXCLUDES: tuple[str, ...] = (
    "Environment",
    "FX",
    "Graphic",
    "Matte Painting",
    "Vehicle",
    "Tool",
    "Font",
)

# Every ``self._sg.*`` call is wrapped with these. ShotGrid's client raises
# Fault for API-level errors; everything else is a network-layer failure that
# an artist should see as a clean "could not reach ShotGrid" message.
_NETWORK_EXCEPTIONS: tuple[type[BaseException], ...] = (
    shotgun_api3.Fault,
    OSError,
    urllib.error.URLError,
    http.client.HTTPException,
    socket.timeout,
)


# ---------------------------------------------------------------------------
# ShotGrid client
# ---------------------------------------------------------------------------


class ShotGrid:
    """ShotGrid client

    Obtain an instance with `connect` — the class caches one connection
    per `SG_Config` so the same process never opens two sockets to the
    same project. Direct `__init__` usage is possible but bypasses the cache.

    Every read method returns a fully-typed entity from
    `pipe.shotgrid.entities` or raises a subclass of
    `ShotGridError`. Every write verb returns the refreshed entity so
    callers do not have to re-fetch.
    """

    _sg: shotgun_api3.Shotgun
    _project_id: int

    _conn_instances: dict[SG_Config, ShotGrid] = {}

    # Backoff *between* failed `upload_movie` attempts.  Three attempts total:
    # initial, then sleep 2s, retry, sleep 4s, retry.
    _UPLOAD_BACKOFF_SECONDS: tuple[float, ...] = (2.0, 4.0)

    # ---- connection --------------------------------------------------------

    @classmethod
    def connect(cls, config: SG_Config) -> ShotGrid:
        """Return the cached ShotGrid connection for `config` or open one.

        Two calls with an equal `SG_Config` return the same instance.
        """
        existing = cls._conn_instances.get(config)
        if existing is not None:
            return existing
        log.debug(f"Opening new ShotGrid connection for project {config.project_id}")
        instance = cls(config)
        cls._conn_instances[config] = instance
        return instance

    def __init__(self, config: SG_Config) -> None:
        self._apply_houdini_shotgrid_upload_runtime_patch()
        ca_certs_path = self._resolve_shotgrid_ca_bundle_for_current_dcc()
        if ca_certs_path:
            log.info(f"ShotGrid CA bundle selected: {ca_certs_path}")
        self._sg = shotgun_api3.Shotgun(
            config.sg_server,
            config.sg_script,
            config.sg_key,
            ca_certs=ca_certs_path,
        )
        self._project_id = config.project_id

    # ---- reads: assets -----------------------------------------------------

    @overload
    def get_asset(self, *, id: int) -> Asset: ...
    @overload
    def get_asset(self, *, name: str) -> Asset: ...
    @overload
    def get_asset(self, *, display_name: str) -> Asset: ...
    @overload
    def get_asset(self, *, path: str) -> Asset: ...

    def get_asset(
        self,
        *,
        id: int | None = None,
        name: str | None = None,
        display_name: str | None = None,
        path: str | None = None,
    ) -> Asset:
        """Fetch one asset by a unique identifier.

        Exactly one of `id` / `name` / `display_name` / `path` must be
        provided.

        Args:
            id: ShotGrid asset id.
            name: Normalized pipeline name (e.g. `"hero_sandwich"`).
            display_name: ShotGrid display name (e.g. `"Hero Sandwich"`).
            path: Canonical asset path (e.g. `"asset/food/hero_sandwich"`).

        Returns:
            The fully-populated `Asset`.

        Raises:
            ShotGridNotFound: No asset matches the selector.
            ShotGridAmbiguous: Multiple assets match. Only possible on `name`
                or `display_name` collisions; `id` and `path` are unique
                by construction.
            TypeError: Zero or more than one selector was provided.
        """
        _require_exactly_one_selector(
            id=id, name=name, display_name=display_name, path=path
        )
        selector, value = _selected(
            id=id, name=name, display_name=display_name, path=path
        )
        filters = self._asset_scope_filters()
        if selector == "id":
            filters.append(("id", "is", value))
        elif selector == "display_name":
            filters.append(("code", "is", value))
        # `name` and `path` are derived fields — no SG-side filter exists.
        # Fetch the scoped list and match in Python.
        rows = _read_or_raise(
            lambda: self._sg.find("Asset", filters, list(_SG_FIELDS_ASSET)),
            entity_type="Asset",
            selector=selector,
            value=value,
        )
        if selector == "name":
            rows = [r for r in rows if normalize_display_name(r.get("code")) == value]
        elif selector == "path":
            rows = [
                r
                for r in rows
                if build_asset_path(r.get("code"), r.get("sg_subdirectory")) == value
            ]
        return self._one_or_raise(
            entity_type="Asset",
            selector=selector,
            value=value,
            rows=rows,
            cls=Asset,
        )

    @ttl_cache(seconds=60)
    def find_assets(
        self,
        *,
        type: str | None = None,
        tags: set[str] | None = None,
        require_all_tags: bool = False,
        roots_only: bool = False,
    ) -> list[Asset]:
        """Return every asset matching the given filters.

        Args:
            type: ShotGrid asset type (`"Character"`, `"Prop"`, ...).
            tags: Restrict to assets carrying these tag strings.
            require_all_tags: If `True`, require every tag in `tags`.
                If `False` (default), match any of them.
            roots_only: If `True`, only return top-level assets (no
                parent). Useful for UI dropdowns that hide variant children.

        Returns:
            A list of `Asset`, possibly empty. Never `None`.
        """
        filters: list[Any] = self._asset_scope_filters()
        if type is not None:
            filters.append(("sg_asset_type", "is", type))
        if tags:
            filters.append(
                {
                    "filter_operator": "all" if require_all_tags else "any",
                    "filters": [("tags", "name_is", t) for t in tags],
                }
            )
        rows = _read_or_raise(
            lambda: self._sg.find("Asset", filters, list(_SG_FIELDS_ASSET)),
            entity_type="Asset",
            selector="filters",
            value=None,
        )
        if roots_only:
            rows = [r for r in rows if not r.get("parents")]
        return self._many(rows, Asset)

    # ---- reads: shots ------------------------------------------------------

    @overload
    def get_shot(self, *, id: int) -> Shot: ...
    @overload
    def get_shot(self, *, code: str) -> Shot: ...

    def get_shot(
        self,
        *,
        id: int | None = None,
        code: str | None = None,
    ) -> Shot:
        """Fetch one shot by id or code (e.g. `"a_020"`).

        Exactly one of `id` or `code` must be provided.

        Raises:
            ShotGridNotFound: No shot matches.
            ShotGridAmbiguous: Multiple shots share a code (should not happen
                in a well-formed project; caller should disambiguate by id).
            TypeError: Zero or more than one selector was provided.
        """
        _require_exactly_one_selector(id=id, code=code)
        selector, value = _selected(id=id, code=code)
        sg_filter = ("id", "is", value) if selector == "id" else ("code", "is", value)
        filters = [self._project_filter(), _SG_STATUS_ACTIVE_FILTER, sg_filter]
        rows = _read_or_raise(
            lambda: self._sg.find("Shot", filters, list(_SG_FIELDS_SHOT)),
            entity_type="Shot",
            selector=selector,
            value=value,
        )
        return self._one_or_raise(
            entity_type="Shot",
            selector=selector,
            value=value,
            rows=rows,
            cls=Shot,
        )

    @ttl_cache(seconds=60)
    def find_shots(
        self,
        *,
        sequence: Sequence | None = None,
    ) -> list[Shot]:
        """Return every shot, optionally restricted to one sequence.

        Args:
            sequence: If given, restrict to shots in this sequence.
        """
        filters: list[Any] = [self._project_filter(), _SG_STATUS_ACTIVE_FILTER]
        if sequence is not None:
            filters.append(("sg_sequence", "is", _entity_ref("Sequence", sequence)))
        rows = _read_or_raise(
            lambda: self._sg.find("Shot", filters, list(_SG_FIELDS_SHOT)),
            entity_type="Shot",
            selector="filters",
            value=None,
        )
        return self._many(rows, Shot)

    # ---- reads: sequences --------------------------------------------------

    def get_sequence(
        self,
        *,
        id: int | None = None,
        code: str | None = None,
    ) -> Sequence:
        """Fetch one sequence by id or code.

        Raises:
            ShotGridNotFound: No sequence matches.
            TypeError: Zero or more than one selector was provided.
        """
        _require_exactly_one_selector(id=id, code=code)
        selector, value = _selected(id=id, code=code)
        sg_filter = ("id", "is", value) if selector == "id" else ("code", "is", value)
        filters = [self._project_filter(), _SG_STATUS_ACTIVE_FILTER, sg_filter]
        rows = _read_or_raise(
            lambda: self._sg.find("Sequence", filters, list(_SG_FIELDS_SEQUENCE)),
            entity_type="Sequence",
            selector=selector,
            value=value,
        )
        return self._one_or_raise(
            entity_type="Sequence",
            selector=selector,
            value=value,
            rows=rows,
            cls=Sequence,
        )

    @ttl_cache(seconds=60)
    def find_sequences(self) -> list[Sequence]:
        """Return every sequence on the project."""
        filters = [self._project_filter(), _SG_STATUS_ACTIVE_FILTER]
        rows = _read_or_raise(
            lambda: self._sg.find("Sequence", filters, list(_SG_FIELDS_SEQUENCE)),
            entity_type="Sequence",
            selector="filters",
            value=None,
        )
        return self._many(rows, Sequence)

    # ---- reads: environments -----------------------------------------------

    @overload
    def get_environment(self, *, id: int) -> Environment: ...
    @overload
    def get_environment(self, *, code: str) -> Environment: ...
    @overload
    def get_environment(self, *, path: str) -> Environment: ...

    def get_environment(
        self,
        *,
        id: int | None = None,
        code: str | None = None,
        path: str | None = None,
    ) -> Environment:
        """Fetch one environment asset by id, code, or canonical path.

        Raises:
            ShotGridNotFound: No environment matches.
            ShotGridAmbiguous: Multiple environments match.
            TypeError: Zero or more than one selector was provided.
        """
        _require_exactly_one_selector(id=id, code=code, path=path)
        selector, value = _selected(id=id, code=code, path=path)
        filters: list[Any] = [
            self._project_filter(),
            _SG_STATUS_ACTIVE_FILTER,
            ("sg_asset_type", "is", "Environment"),
        ]
        if selector == "id":
            filters.append(("id", "is", value))
        elif selector == "code":
            filters.append(("code", "is", value))
        rows = _read_or_raise(
            lambda: self._sg.find("Asset", filters, list(_SG_FIELDS_ENVIRONMENT)),
            entity_type="Environment",
            selector=selector,
            value=value,
        )
        if selector == "path":
            rows = [
                r
                for r in rows
                if build_environment_path(r.get("code"), r.get("sg_subdirectory"))
                == value
            ]
        return self._one_or_raise(
            entity_type="Environment",
            selector=selector,
            value=value,
            rows=rows,
            cls=Environment,
        )

    @ttl_cache(seconds=60)
    def find_environments(self) -> list[Environment]:
        """Return every environment asset on the project."""
        filters = [
            self._project_filter(),
            _SG_STATUS_ACTIVE_FILTER,
            ("sg_asset_type", "is", "Environment"),
        ]
        rows = _read_or_raise(
            lambda: self._sg.find("Asset", filters, list(_SG_FIELDS_ENVIRONMENT)),
            entity_type="Environment",
            selector="filters",
            value=None,
        )
        return self._many(rows, Environment)

    # ---- reads: users ------------------------------------------------------

    @overload
    def get_user(self, *, id: int) -> User: ...
    @overload
    def get_user(self, *, login: str) -> User: ...
    @overload
    def get_user(self, *, name: str) -> User: ...

    def get_user(
        self,
        *,
        id: int | None = None,
        login: str | None = None,
        name: str | None = None,
    ) -> User:
        """Fetch one ShotGrid `HumanUser` by id, login, or display name.

        Args:
            id: ShotGrid user id.
            login: The user's ShotGrid login (usually an email address).
            name: The user's display name (may not be unique — prefer `login`).

        Raises:
            ShotGridNotFound: No user matches.
            ShotGridAmbiguous: Multiple users share the selector. Common on
                `name`; rare on `login`; impossible on `id`.
            TypeError: Zero or more than one selector was provided.
        """
        _require_exactly_one_selector(id=id, login=login, name=name)
        selector, value = _selected(id=id, login=login, name=name)
        filters: list[Any] = [_SG_STATUS_ACTIVE_USER_FILTER]
        if selector == "id":
            filters.append(("id", "is", value))
        elif selector == "login":
            filters.append(("login", "is", value))
        else:  # name
            filters.append(("name", "is", value))
        rows = _read_or_raise(
            lambda: self._sg.find("HumanUser", filters, list(_SG_FIELDS_USER)),
            entity_type="User",
            selector=selector,
            value=value,
        )
        return self._one_or_raise(
            entity_type="User",
            selector=selector,
            value=value,
            rows=rows,
            cls=User,
        )

    @ttl_cache(seconds=60)
    def find_users(self) -> list[User]:
        """Return every active ShotGrid `HumanUser`. Not project-scoped."""
        rows = _read_or_raise(
            lambda: self._sg.find(
                "HumanUser",
                [_SG_STATUS_ACTIVE_USER_FILTER],
                list(_SG_FIELDS_USER),
            ),
            entity_type="User",
            selector="filters",
            value=None,
        )
        return self._many(rows, User)

    # ---- reads: tasks ------------------------------------------------------

    def get_task(self, *, id: int) -> Task:
        """Fetch one task by id.

        Tasks do not carry a unique code in this pipeline, so id is the only
        stable selector. Use `find_tasks` to search by shot or user.

        Raises:
            ShotGridNotFound: No task matches.
        """
        filters = [self._project_filter(), ("id", "is", id)]
        rows = _read_or_raise(
            lambda: self._sg.find("Task", filters, list(_SG_FIELDS_TASK)),
            entity_type="Task",
            selector="id",
            value=id,
        )
        return self._one_or_raise(
            entity_type="Task",
            selector="id",
            value=id,
            rows=rows,
            cls=Task,
        )

    @ttl_cache(seconds=60)
    def find_tasks(
        self,
        *,
        shot: Shot | None = None,
        asset: Asset | None = None,
        user: User | None = None,
    ) -> list[Task]:
        """Return tasks matching the given filters.

        Args:
            shot: Restrict to tasks on this shot.
            asset: Restrict to tasks on this asset.
            user: Restrict to tasks assigned to this user.
        """
        filters: list[Any] = [self._project_filter()]
        if shot is not None:
            filters.append(("entity", "is", _entity_ref("Shot", shot)))
        if asset is not None:
            filters.append(("entity", "is", _entity_ref("Asset", asset)))
        if user is not None:
            filters.append(("task_assignees", "in", [_entity_ref("HumanUser", user)]))
        rows = _read_or_raise(
            lambda: self._sg.find("Task", filters, list(_SG_FIELDS_TASK)),
            entity_type="Task",
            selector="filters",
            value=None,
        )
        return self._many(rows, Task)

    # ---- reads: playlists --------------------------------------------------

    def get_playlist(self, *, id: int) -> Playlist:
        """Fetch one review playlist by id.

        Raises:
            ShotGridNotFound: No playlist matches.
        """
        filters = [self._project_filter(), ("id", "is", id)]
        rows = _read_or_raise(
            lambda: self._sg.find("Playlist", filters, list(_SG_FIELDS_PLAYLIST)),
            entity_type="Playlist",
            selector="id",
            value=id,
        )
        return self._one_or_raise(
            entity_type="Playlist",
            selector="id",
            value=id,
            rows=rows,
            cls=Playlist,
        )

    @ttl_cache(seconds=60)
    def find_recent_playlists(self, *, limit: int = 10) -> list[Playlist]:
        """Return the most recently updated review playlists, newest first.

        Args:
            limit: How many rows to return. Defaults to 10.
        """
        filters = [self._project_filter()]
        rows = _read_or_raise(
            lambda: self._sg.find(
                "Playlist",
                filters,
                list(_SG_FIELDS_PLAYLIST),
                order=[{"field_name": "updated_at", "direction": "desc"}],
                limit=limit,
            ),
            entity_type="Playlist",
            selector="filters",
            value=None,
        )
        return self._many(rows, Playlist)

    @ttl_cache(seconds=60)
    def find_playlists(
        self,
        *,
        code_contains: str | None = None,
    ) -> list[Playlist]:
        """Return playlists matching the given filters.

        Args:
            code_contains: Restrict to playlists whose `code` contains this
                substring (e.g. `"Lighting"` for "Lighting Dailies").

        Returns:
            A list of `Playlist`, possibly empty.
        """
        filters: list[Any] = [self._project_filter()]
        if code_contains:
            filters.append(("code", "contains", code_contains))
        rows = _read_or_raise(
            lambda: self._sg.find("Playlist", filters, list(_SG_FIELDS_PLAYLIST)),
            entity_type="Playlist",
            selector="filters",
            value=None,
        )
        return self._many(rows, Playlist)

    # ---- writes: assets ----------------------------------------------------

    def add_material_variant(self, asset: Asset, name: str) -> Asset:
        """Register `name` as a material variant on `asset`.

        Idempotent: calling twice with the same name is a no-op on the second
        call and still returns the refreshed asset. Writes are atomic — a
        failure leaves the ShotGrid record unchanged.

        Returns:
            The refreshed `Asset` with `name` in `material_variants`.

        Raises:
            ShotGridWriteError: ShotGrid rejected the update.
        """
        return self._edit_asset_csv_field(
            asset,
            field="sg_material_variants",
            name=name,
            add=True,
        )

    def remove_material_variant(self, asset: Asset, name: str) -> Asset:
        """Unregister `name` as a material variant on `asset`. Idempotent."""
        return self._edit_asset_csv_field(
            asset,
            field="sg_material_variants",
            name=name,
            add=False,
        )

    def add_geometry_variant(self, asset: Asset, name: str) -> Asset:
        """Register `name` as a geometry variant on `asset`. Idempotent."""
        return self._edit_asset_csv_field(
            asset,
            field="sg_geometry_variants",
            name=name,
            add=True,
        )

    def remove_geometry_variant(self, asset: Asset, name: str) -> Asset:
        """Unregister `name` as a geometry variant on `asset`. Idempotent."""
        return self._edit_asset_csv_field(
            asset,
            field="sg_geometry_variants",
            name=name,
            add=False,
        )

    def add_material_layer(self, asset: Asset, name: str) -> Asset:
        """Register `name` as a material layer on `asset`. Idempotent."""
        return self._edit_asset_csv_field(
            asset,
            field="sg_material_layers",
            name=name,
            add=True,
        )

    def remove_material_layer(self, asset: Asset, name: str) -> Asset:
        """Unregister `name` as a material layer on `asset`. Idempotent."""
        return self._edit_asset_csv_field(
            asset,
            field="sg_material_layers",
            name=name,
            add=False,
        )

    def set_asset_subdirectory(self, asset: Asset, subdirectory: str | None) -> Asset:
        """Set the asset's on-disk subdirectory (e.g. `"char"`, `"prop"`).

        Pass `None` to clear. Idempotent.

        Raises:
            ShotGridWriteError: ShotGrid rejected the update.
        """
        if asset.subdirectory == subdirectory:
            return self.reload(asset)
        payload = {"sg_subdirectory": subdirectory if subdirectory is not None else ""}
        _write_or_raise(
            lambda: self._sg.update("Asset", asset.id, payload),
            entity_type="Asset",
            entity_id=asset.id,
            field="sg_subdirectory",
        )
        invalidate(self)
        return self.reload(asset)

    def _edit_asset_csv_field(
        self,
        asset: Asset,
        *,
        field: str,
        name: str,
        add: bool,
    ) -> Asset:
        """Add or remove `name` from one of the CSV-set fields on `asset`.

        Shared impl for `add_material_variant` / `add_geometry_variant` /
        `add_material_layer` and their `remove_*` counterparts.

        Note: read-modify-write on a comma-separated string is *not* safe
        against concurrent writers. If concurrent variant writes become a real
        problem, move the underlying ShotGrid field to a proper multi-entity
        relationship.
        """
        _CSV_ATTR_BY_FIELD = {
            "sg_material_variants": "material_variants",
            "sg_geometry_variants": "geometry_variants",
            "sg_material_layers": "material_layers",
        }
        local_attr = _CSV_ATTR_BY_FIELD[field]
        current: set[str] = getattr(asset, local_attr) or set()
        if (name in current) == add:
            return self.reload(asset)
        new_values = current | {name} if add else current - {name}
        _write_or_raise(
            lambda: self._sg.update(
                "Asset", asset.id, {field: ",".join(sorted(new_values))}
            ),
            entity_type="Asset",
            entity_id=asset.id,
            field=field,
        )
        invalidate(self)
        return self.reload(asset)

    # ---- versions ----------------------------------------------------------

    def create_shot_version(
        self,
        shot: Shot,
        *,
        code: str,
        user: User | None = None,
        task: Task | None = None,
        video: Path | str | None = None,
        description: str | None = None,
        playlist: Playlist | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> Version:
        """Create a ShotGrid `Version` linked to `shot`.

        Args:
            shot: The shot this version is for.
            code: The Version code (display name).
            user: Optional artist who created the version.
            task: Optional task this version belongs to.
            video: Optional path to a movie; if given, uploads it via
                `upload_movie` after the Version row is created.
            description: Optional artist-authored description.
            playlist: If given, the created Version is linked to this playlist.
            extra_fields: Escape hatch for one-off SG field writes. Prefer
                extending `pipe.shotgrid.entities.Version` over using
                this.

        Raises:
            ShotGridWriteError: ShotGrid rejected the Version create or a
                downstream upload / link step failed.
        """
        return self._create_version(
            parent_type="Shot",
            parent=shot,
            code=code,
            user=user,
            task=task,
            video=video,
            description=description,
            playlist=playlist,
            extra_fields=extra_fields,
        )

    def create_asset_version(
        self,
        asset: Asset,
        *,
        code: str,
        user: User | None = None,
        task: Task | None = None,
        video: Path | str | None = None,
        description: str | None = None,
        playlist: Playlist | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> Version:
        """Create a ShotGrid `Version` linked to `asset`.

        See `create_shot_version` for argument semantics — only the
        parent entity type differs.

        Raises:
            ShotGridWriteError: The create, upload, or link failed.
        """
        return self._create_version(
            parent_type="Asset",
            parent=asset,
            code=code,
            user=user,
            task=task,
            video=video,
            description=description,
            playlist=playlist,
            extra_fields=extra_fields,
        )

    def _create_version(
        self,
        *,
        parent_type: str,
        parent: Shot | Asset,
        code: str,
        user: User | None,
        task: Task | None,
        video: Path | str | None,
        description: str | None,
        playlist: Playlist | None,
        extra_fields: dict[str, Any] | None,
    ) -> Version:
        payload: dict[str, Any] = {
            "code": code,
            "entity": {"type": parent_type, "id": parent.id},
            "project": {"type": "Project", "id": self._project_id},
        }
        if user is not None:
            payload["user"] = _entity_ref("HumanUser", user)
        if task is not None:
            payload["sg_task"] = _entity_ref("Task", task)
        if description is not None:
            payload["description"] = description
        if extra_fields:
            conflicts = set(extra_fields) & set(payload)
            if conflicts:
                raise ValueError(
                    f"extra_fields cannot override structured keys: {sorted(conflicts)}"
                )
            payload.update(extra_fields)
        row = _write_or_raise(
            lambda: self._sg.create("Version", payload, list(_SG_FIELDS_VERSION)),
            entity_type="Version",
            entity_id=None,
            field=None,
        )
        version = Version.from_sg(row)
        self._attach_db(version)
        object.__setattr__(version, "_hydrated", True)
        if video is not None:
            version = self.upload_movie(version, video)
        if playlist is not None:
            self.link_to_playlist(version, playlist)
            version = self.reload(version)
        invalidate(self)
        return version

    # ---- uploads -----------------------------------------------------------

    def upload_movie(self, version: Version, path: Path | str) -> Version:
        """Upload a movie file to an existing `Version` row.

        Performs three things atomically from the caller's perspective:

        1. Uploads the file to `sg_uploaded_movie` (the SG-hosted attachment).
        2. Writes the same path to `sg_path_to_frames` (the source-on-disk
           text field) so the two never drift apart.
        3. Reloads the Version and returns the refreshed entity.

        The upload call retries on transient network failures.  Three attempts
        total, with 2s and 4s waits between them; the final failure raises
        `ShotGridWriteError` and preserves the underlying
        `shotgun_api3.Fault` as `__cause__` for developer debugging.

        Args:
            version: The Version to upload against.
            path: Path to the movie file on disk.

        Returns:
            The refreshed `Version` with both `sg_uploaded_movie`
            and `sg_path_to_frames` populated.

        Raises:
            ShotGridWriteError: Every upload attempt failed, or the
                `sg_path_to_frames` follow-up write failed.
        """
        path_str = str(path)
        self._upload_movie_with_retry(version, path_str)
        _write_or_raise(
            lambda: self._sg.update(
                "Version", version.id, {"sg_path_to_frames": path_str}
            ),
            entity_type="Version",
            entity_id=version.id,
            field="sg_path_to_frames",
        )
        return self.reload(version)

    def _upload_movie_with_retry(self, version: Version, path_str: str) -> None:
        """Run `self._sg.upload` with the configured backoff schedule."""
        backoffs = self._UPLOAD_BACKOFF_SECONDS
        total_attempts = len(backoffs) + 1
        last_exc: BaseException | None = None
        for attempt in range(1, total_attempts + 1):
            try:
                self._sg.upload(
                    "Version",
                    version.id,
                    path_str,
                    field_name="sg_uploaded_movie",
                )
                return
            except _NETWORK_EXCEPTIONS as exc:
                last_exc = exc
                if attempt == total_attempts:
                    break
                wait = backoffs[attempt - 1]
                log.warning(
                    "upload_movie attempt %d/%d failed for Version id=%d; "
                    "retrying in %ss: %s",
                    attempt,
                    total_attempts,
                    version.id,
                    wait,
                    exc,
                )
                time.sleep(wait)
        raise ShotGridWriteError(
            entity_type="Version",
            entity_id=version.id,
            field="sg_uploaded_movie",
            cause=last_exc,
        ) from last_exc

    # ---- playlist links ----------------------------------------------------

    def link_to_playlist(self, version: Version, playlist: Playlist) -> Playlist:
        """Add `version` to `playlist` without replacing existing members.

        Uses ShotGrid's `multi_entity_update_modes={"versions": "add"}`
        so other versions already on the playlist are preserved.

        Returns:
            The refreshed `Playlist`.

        Raises:
            ShotGridWriteError: ShotGrid rejected the link.
        """
        _write_or_raise(
            lambda: self._sg.update(
                "Playlist",
                playlist.id,
                {"versions": [{"type": "Version", "id": version.id}]},
                multi_entity_update_modes={"versions": "add"},
            ),
            entity_type="Playlist",
            entity_id=playlist.id,
            field="versions",
        )
        invalidate(self)
        return self.reload(playlist)

    # ---- refresh -----------------------------------------------------------

    def reload(self, entity: _E) -> _E:
        """Re-fetch `entity` from ShotGrid and return the fresh copy.

        Used by write verbs and by partial-entity lazy fetch. Safe for callers
        to use directly after out-of-band writes (e.g. another tool changed
        the asset while this tool held a stale reference).

        Raises:
            ShotGridNotFound: `entity.id` no longer exists in ShotGrid.
        """
        if isinstance(entity, Asset):
            return cast(_E, self.get_asset(id=entity.id))
        if isinstance(entity, Environment):
            return cast(_E, self.get_environment(id=entity.id))
        if isinstance(entity, Shot):
            return cast(_E, self.get_shot(id=entity.id))
        if isinstance(entity, Sequence):
            return cast(_E, self.get_sequence(id=entity.id))
        if isinstance(entity, User):
            return cast(_E, self.get_user(id=entity.id))
        if isinstance(entity, Task):
            return cast(_E, self.get_task(id=entity.id))
        if isinstance(entity, Playlist):
            return cast(_E, self.get_playlist(id=entity.id))
        if isinstance(entity, Version):
            return cast(_E, self._reload_version(entity))
        raise TypeError(f"Cannot reload unknown entity type: {type(entity).__name__}")

    def _reload_version(self, version: Version) -> Version:
        """Re-fetch a Version by id. Version has no public `get_version`
        because callers hold the Version returned by `create_*_version` /
        `upload_movie`; this exists for the lazy-fetch + reload paths."""
        rows = _read_or_raise(
            lambda: self._sg.find(
                "Version",
                [self._project_filter(), ("id", "is", version.id)],
                list(_SG_FIELDS_VERSION),
            ),
            entity_type="Version",
            selector="id",
            value=version.id,
        )
        return self._one_or_raise(
            entity_type="Version",
            selector="id",
            value=version.id,
            rows=rows,
            cls=Version,
        )

    # ---- internals: query + hydration helpers -----------------------------

    def _project_filter(self) -> tuple[str, str, dict[str, Any]]:
        """The filter fragment that scopes every project-local query."""
        return ("project", "is", {"type": "Project", "id": self._project_id})

    def _asset_scope_filters(self) -> list[Any]:
        """Filters shared by every Asset query — project, status, type excludes."""
        return [
            self._project_filter(),
            _SG_STATUS_ACTIVE_FILTER,
            {
                "filter_operator": "all",
                "filters": [
                    ("sg_asset_type", "is_not", t) for t in _SG_ASSET_TYPE_EXCLUDES
                ],
            },
        ]

    def _attach_db(self, value: Any) -> None:
        """Recursively wire this connection onto every `SGEntity` in `value`.

        Uses `object.__getattribute__` to read fields directly, so walking a
        partial entity does not itself trigger lazy hydration.
        """
        if isinstance(value, SGEntity):
            object.__setattr__(value, "_db", self)
            for f in attrs.fields(type(value)):
                if f.name.startswith("_"):
                    continue
                self._attach_db(object.__getattribute__(value, f.name))
        elif isinstance(value, list):
            for item in value:
                self._attach_db(item)

    def _one_or_raise(
        self,
        *,
        entity_type: str,
        selector: str,
        value: object,
        rows: list[dict[str, Any]],
        cls: type[_E],
    ) -> _E:
        """Convert a SG result list into exactly one hydrated entity, or raise."""
        if not rows:
            raise ShotGridNotFound(
                entity_type=entity_type, selector=selector, value=value
            )
        if len(rows) > 1:
            raise ShotGridAmbiguous(
                entity_type=entity_type,
                selector=selector,
                value=value,
                matching_ids=[r["id"] for r in rows],
            )
        entity = cls.from_sg(rows[0])
        self._attach_db(entity)
        # Root entities born from a full SG row never need to lazy-fetch
        # themselves; only nested partial refs inside them do.
        object.__setattr__(entity, "_hydrated", True)
        return entity

    def _many(self, rows: list[dict[str, Any]], cls: type[_E]) -> list[_E]:
        """Structure a ShotGrid result list into entities with `_db` attached."""
        result = [cls.from_sg(r) for r in rows]
        for e in result:
            self._attach_db(e)
            object.__setattr__(e, "_hydrated", True)
        return result

    # ---- internals: Houdini SSL workaround --------------------------------
    # DCC workaround: Houdini's bundled Python ships without modern SSL, so
    # ``shotgun_api3`` uploads fail silently. The following two methods are
    # lifted verbatim from the legacy ``sgaadb.py``. Do not refactor without
    # re-testing uploads inside Houdini.

    @staticmethod
    def _is_houdini_runtime() -> bool:
        return os.environ.get("DCC", "").strip().lower() == "houdini"

    @classmethod
    def _apply_houdini_shotgrid_upload_runtime_patch(cls) -> None:
        """Apply a Houdini-only runtime fix for ShotGrid upload HTTPS classes.

        We intentionally keep the vendored ``shotgun_api3`` source unmodified
        and patch the legacy upload HTTPS classes at runtime instead.
        """
        if not cls._is_houdini_runtime():
            return

        from shotgun_api3 import shotgun as shotgun_module

        if getattr(shotgun_module, "_PIPELINE_HOUDINI_UPLOAD_PATCHED", False):
            return

        class _PipelineCACertsHTTPSConnection(http.client.HTTPConnection):
            """Drop-in replacement with Python 3.11-compatible super() usage."""

            default_port = http.client.HTTPS_PORT

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.__ca_certs = kwargs.pop("ca_certs")
                super().__init__(*args, **kwargs)

            def connect(self) -> None:
                # Python 3.11+ only — the pre-3.8 `ssl.wrap_socket` path from
                # the legacy implementation was unreachable and has been dropped.
                super().connect()
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.verify_mode = ssl.CERT_REQUIRED
                context.check_hostname = False
                if self.__ca_certs:
                    context.load_verify_locations(self.__ca_certs)
                self.sock = context.wrap_socket(self.sock)

        class _PipelineCACertsHTTPSHandler(urllib.request.HTTPSHandler):
            """HTTPS opener that routes upload requests through custom CA certs."""

            def __init__(self, ca_certs: str | None) -> None:
                super().__init__()
                self.__ca_certs = ca_certs

            def https_open(self, req: urllib.request.Request) -> Any:
                return self.do_open(self.create_https_connection, req)

            def create_https_connection(self, *args: Any, **kwargs: Any) -> Any:
                return _PipelineCACertsHTTPSConnection(
                    *args,
                    ca_certs=self.__ca_certs,
                    **kwargs,
                )

        shotgun_module.CACertsHTTPSConnection = _PipelineCACertsHTTPSConnection
        shotgun_module.CACertsHTTPSHandler = _PipelineCACertsHTTPSHandler
        shotgun_module._PIPELINE_HOUDINI_UPLOAD_PATCHED = True
        log.info("Applied Houdini ShotGrid upload HTTPS runtime compatibility patch.")

    @classmethod
    def _resolve_shotgrid_ca_bundle_for_current_dcc(cls) -> str | None:
        """Resolve CA bundle path for DCCs that need explicit TLS trust config."""
        if not cls._is_houdini_runtime():
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


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _require_exactly_one_selector(**selectors: object) -> None:
    """Raise `TypeError` unless exactly one kwarg is non-`None`.

    Used by every `get_*` method so callers see a TypeError at the call
    site (before any ShotGrid traffic) when they supply zero or multiple
    selectors.
    """
    provided = [name for name, value in selectors.items() if value is not None]
    if len(provided) == 1:
        return
    names = ", ".join(sorted(selectors))
    if not provided:
        raise TypeError(f"Provide exactly one of {names}; got none.")
    raise TypeError(
        f"Provide exactly one of {names}; got multiple: {sorted(provided)}."
    )


def _selected(**selectors: object) -> tuple[str, object]:
    """Return `(selector_name, value)` for the one non-None selector.

    Assumes `_require_exactly_one_selector` has already run.
    """
    for name, value in selectors.items():
        if value is not None:
            return name, value
    # _require_exactly_one_selector has already validated; this is unreachable.
    raise AssertionError("selector guard failed")


def _entity_ref(sg_type: str, entity: SGEntity | None) -> dict[str, Any] | None:
    """Build a `{'type': ..., 'id': ...}` link-ref or `None`."""
    if entity is None:
        return None
    return {"type": sg_type, "id": entity.id}


def _read_or_raise(
    call: Callable[[], _T],
    *,
    entity_type: str,
    selector: str,
    value: object,
) -> _T:
    """Run a ShotGrid read call; wrap network/Fault errors as `ShotGridError`."""
    try:
        return call()
    except _NETWORK_EXCEPTIONS as exc:
        raise ShotGridError(
            f"ShotGrid read failed for {entity_type} where {selector}={value!r}: {exc}"
        ) from exc


def _write_or_raise(
    call: Callable[[], _T],
    *,
    entity_type: str,
    entity_id: int | None,
    field: str | None,
) -> _T:
    """Run a ShotGrid write call; wrap network/Fault errors as `ShotGridWriteError`."""
    try:
        return call()
    except _NETWORK_EXCEPTIONS as exc:
        raise ShotGridWriteError(
            entity_type=entity_type,
            entity_id=entity_id,
            field=field,
            cause=exc,
        ) from exc
