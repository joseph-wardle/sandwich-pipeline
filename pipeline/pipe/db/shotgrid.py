"""ShotGrid client for `sandwich-pipeline`.

This module is the single entry point for every pipeline interaction with
ShotGrid ((a.k.a ShotGun Studio (a.k.a. Autodesk Flow Production Tracking)).
Raw `shotgun_api3` dicts, filters, and `Fault` exceptions never leave this file.

Read this file top-to-bottom to understand the full ShotGrid surface the
pipeline uses. Sections, in order:

* Connection and configuration (`SG_Config`, `ChildMode`, `ShotGrid`).
* Read verbs - `get_*` (single entity) and `find_*` (list of entities).
* Write verbs — first-class, idempotent, return the refreshed entity.
* Version creation, movie upload, playlist linking.
* Internals — singleton cache, Houdini SSL workaround, selector validation.
"""

from __future__ import annotations

import http.client
import logging
import os
import ssl
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

import shotgun_api3

from pipe.struct.db import (
    Asset,
    Environment,
    Playlist,
    Sequence,
    Shot,
    Task,
    User,
    Version,
)

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(eq=True, frozen=True)
class SG_Config:
    """Credentials and project id needed to open a ShotGrid connection.

    `sg_key` is equivalent to an admin password. Never commit it, never log
    it, never include it in error messages. In production this is loaded from
    the gitignored `env_sg.py` synced by the ``post-checkout`` git hook.
    """

    project_id: int
    sg_key: str
    sg_script: str
    sg_server: str


class ChildMode(Enum):
    """How asset queries treat parent/child asset relationships.

    Replaces the legacy `DBInterface.ChildQueryMode` enum. Only shapes
    asset queries — no other entity type has parent/child in this pipeline.
    """

    # Leaf assets plus top-level assets that have no children.
    LEAVES = 0
    # Every asset, regardless of relationship.
    ALL = 1
    # Only assets that have a parent.
    CHILDREN = 2
    # Only assets that have at least one child.
    PARENTS = 3
    # Top-level assets regardless of whether they have children.
    ROOTS = 4


# ---------------------------------------------------------------------------
# ShotGrid client
# ---------------------------------------------------------------------------


class ShotGrid:
    """Typed, self-documenting ShotGrid client scoped to a single project.

    Obtain an instance with :meth:`connect` — the class caches one connection
    per :class:`SG_Config` so the same process never opens two sockets to the
    same project. Direct ``__init__`` usage is possible but bypasses the cache.

    Every read method returns a fully-typed entity from ``pipe.struct.db`` or
    raises a subclass of :class:`ShotGridError`. Every write verb returns the
    refreshed entity so callers do not have to re-fetch.
    """

    _sg: shotgun_api3.Shotgun
    _project_id: int

    _conn_instances: dict[SG_Config, ShotGrid] = {}

    # ---- connection --------------------------------------------------------

    @classmethod
    def connect(cls, config: SG_Config) -> ShotGrid:
        """Return the cached ShotGrid connection for ``config`` or open one.

        Two calls with an equal :class:`SG_Config` return the same instance.
        """
        existing = cls._conn_instances.get(config)
        if existing is not None:
            return existing
        log.debug("Opening new ShotGrid connection for project %d", config.project_id)
        instance = cls(config)
        cls._conn_instances[config] = instance
        return instance

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
        self._project_id = config.project_id

    # ---- reads: assets -----------------------------------------------------

    def get_asset(
        self,
        *,
        id: int | None = None,
        name: str | None = None,
        display_name: str | None = None,
        path: str | None = None,
    ) -> Asset:
        """Fetch one asset by a unique identifier.

        Exactly one of ``id`` / ``name`` / ``display_name`` / ``path`` must be
        provided.

        Args:
            id: ShotGrid asset id.
            name: Normalized pipeline name (e.g. ``"hero_sandwich"``).
            display_name: ShotGrid display name (e.g. ``"Hero Sandwich"``).
            path: Canonical asset path (e.g. ``"asset/char/hero_sandwich"``).

        Returns:
            The fully-populated :class:`Asset`.

        Raises:
            ShotGridNotFound: No asset matches the selector.
            ShotGridAmbiguous: Multiple assets match. Only possible on ``name``
                or ``display_name`` collisions; ``id`` and ``path`` are unique
                by construction.
            TypeError: Zero or more than one selector was provided.
        """
        _require_exactly_one_selector(
            id=id, name=name, display_name=display_name, path=path
        )
        raise NotImplementedError("Phase 0 skeleton")

    def find_assets(
        self,
        *,
        type: str | None = None,
        tags: set[str] | None = None,
        require_all_tags: bool = False,
        child_mode: ChildMode = ChildMode.LEAVES,
    ) -> list[Asset]:
        """Return every asset matching the given filters.

        Args:
            type: ShotGrid asset type (``"Character"``, ``"Prop"``, ...).
            tags: Restrict to assets carrying these tag strings.
            require_all_tags: If ``True``, require every tag in ``tags``.
                If ``False`` (default), match any of them.
            child_mode: How to treat parent/child relationships. See
                :class:`ChildMode`.

        Returns:
            A list of :class:`Asset`, possibly empty. Never ``None``.
        """
        raise NotImplementedError("Phase 0 skeleton")

    # ---- reads: shots ------------------------------------------------------

    def get_shot(
        self,
        *,
        id: int | None = None,
        code: str | None = None,
    ) -> Shot:
        """Fetch one shot by id or code (e.g. ``"a10_20"``).

        Exactly one of ``id`` or ``code`` must be provided.

        Raises:
            ShotGridNotFound: No shot matches.
            ShotGridAmbiguous: Multiple shots share a code (should not happen
                in a well-formed project; caller should disambiguate by id).
            TypeError: Zero or more than one selector was provided.
        """
        _require_exactly_one_selector(id=id, code=code)
        raise NotImplementedError("Phase 0 skeleton")

    def find_shots(
        self,
        *,
        sequence: Sequence | None = None,
    ) -> list[Shot]:
        """Return every shot, optionally restricted to one sequence.

        Args:
            sequence: If given, restrict to shots in this sequence.
        """
        raise NotImplementedError("Phase 0 skeleton")

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
        raise NotImplementedError("Phase 0 skeleton")

    def find_sequences(self) -> list[Sequence]:
        """Return every sequence on the project."""
        raise NotImplementedError("Phase 0 skeleton")

    # ---- reads: environments -----------------------------------------------

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
        raise NotImplementedError("Phase 0 skeleton")

    def find_environments(self) -> list[Environment]:
        """Return every environment asset on the project."""
        raise NotImplementedError("Phase 0 skeleton")

    # ---- reads: users ------------------------------------------------------

    def get_user(
        self,
        *,
        id: int | None = None,
        login: str | None = None,
        name: str | None = None,
    ) -> User:
        """Fetch one ShotGrid ``HumanUser`` by id, login, or display name.

        Args:
            id: ShotGrid user id.
            login: The user's ShotGrid login (usually an email address).
            name: The user's display name (may not be unique — prefer ``login``).

        Raises:
            ShotGridNotFound: No user matches.
            ShotGridAmbiguous: Multiple users share the selector. Common on
                ``name``; rare on ``login``; impossible on ``id``.
            TypeError: Zero or more than one selector was provided.
        """
        _require_exactly_one_selector(id=id, login=login, name=name)
        raise NotImplementedError("Phase 0 skeleton")

    def find_users(self) -> list[User]:
        """Return every active ShotGrid ``HumanUser`` on the project."""
        raise NotImplementedError("Phase 0 skeleton")

    # ---- reads: tasks ------------------------------------------------------

    def get_task(self, *, id: int) -> Task:
        """Fetch one task by id.

        Tasks do not carry a unique code in this pipeline, so id is the only
        stable selector. Use :meth:`find_tasks` to search by shot or user.

        Raises:
            ShotGridNotFound: No task matches.
        """
        raise NotImplementedError("Phase 0 skeleton")

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
        raise NotImplementedError("Phase 0 skeleton")

    # ---- reads: playlists --------------------------------------------------

    def get_playlist(self, *, id: int) -> Playlist:
        """Fetch one review playlist by id.

        Raises:
            ShotGridNotFound: No playlist matches.
        """
        raise NotImplementedError("Phase 0 skeleton")

    def find_recent_playlists(self, *, limit: int = 10) -> list[Playlist]:
        """Return the most recently updated active review playlists.

        Args:
            limit: How many rows to return. Defaults to 10.
        """
        raise NotImplementedError("Phase 0 skeleton")

    # ---- writes: assets ----------------------------------------------------

    def add_material_variant(self, asset: Asset, name: str) -> Asset:
        """Register ``name`` as a material variant on ``asset``.

        Idempotent: calling twice with the same name is a no-op on the second
        call and still returns the refreshed asset. Writes are atomic — a
        failure leaves the ShotGrid record unchanged.

        Returns:
            The refreshed :class:`Asset` with ``name`` in ``material_variants``.

        Raises:
            ShotGridWriteError: ShotGrid rejected the update.
        """
        raise NotImplementedError("Phase 0 skeleton")

    def remove_material_variant(self, asset: Asset, name: str) -> Asset:
        """Unregister ``name`` as a material variant on ``asset``. Idempotent."""
        raise NotImplementedError("Phase 0 skeleton")

    def add_geometry_variant(self, asset: Asset, name: str) -> Asset:
        """Register ``name`` as a geometry variant on ``asset``. Idempotent."""
        raise NotImplementedError("Phase 0 skeleton")

    def remove_geometry_variant(self, asset: Asset, name: str) -> Asset:
        """Unregister ``name`` as a geometry variant on ``asset``. Idempotent."""
        raise NotImplementedError("Phase 0 skeleton")

    def add_material_layer(self, asset: Asset, name: str) -> Asset:
        """Register ``name`` as a material layer on ``asset``. Idempotent."""
        raise NotImplementedError("Phase 0 skeleton")

    def remove_material_layer(self, asset: Asset, name: str) -> Asset:
        """Unregister ``name`` as a material layer on ``asset``. Idempotent."""
        raise NotImplementedError("Phase 0 skeleton")

    def set_asset_subdirectory(self, asset: Asset, subdirectory: str | None) -> Asset:
        """Set the asset's on-disk subdirectory (e.g. ``"char"``, ``"prop"``).

        Pass ``None`` to clear. Idempotent.

        Raises:
            ShotGridWriteError: ShotGrid rejected the update.
        """
        raise NotImplementedError("Phase 0 skeleton")

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
        """Create a ShotGrid :class:`Version` linked to ``shot``.

        Args:
            shot: The shot this version is for.
            code: The Version code (display name).
            user: Optional artist who created the version.
            task: Optional task this version belongs to.
            video: Optional path to a movie; if given, uploads it via
                :meth:`upload_movie` after the Version row is created.
            description: Optional artist-authored description.
            playlist: If given, the created Version is linked to this playlist.
            extra_fields: Escape hatch for one-off SG field writes. Prefer
                extending :class:`pipe.struct.db.Version` over using this.

        Raises:
            ShotGridWriteError: ShotGrid rejected the Version create or a
                downstream upload / link step failed.
        """
        raise NotImplementedError("Phase 0 skeleton")

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
        """Create a ShotGrid :class:`Version` linked to ``asset``.

        See :meth:`create_shot_version` for argument semantics — only the
        parent entity type differs.

        Raises:
            ShotGridWriteError: The create, upload, or link failed.
        """
        raise NotImplementedError("Phase 0 skeleton")

    # ---- uploads -----------------------------------------------------------

    def upload_movie(self, version: Version, path: Path | str) -> Version:
        """Upload a movie file to an existing :class:`Version` row.

        Retries the underlying ``shotgun_api3.upload`` call up to three times
        with exponential backoff (2s, 4s, 8s). The final failure raises
        :class:`ShotGridWriteError` and preserves the underlying
        ``shotgun_api3.Fault`` as ``__cause__`` for developer debugging.

        Args:
            version: The Version to upload against.
            path: Path to the movie file on disk.

        Returns:
            The refreshed :class:`Version` with its ``sg_uploaded_movie``
            field populated.

        Raises:
            ShotGridWriteError: Every retry failed.
        """
        raise NotImplementedError("Phase 0 skeleton")

    # ---- playlist links ----------------------------------------------------

    def link_to_playlist(self, version: Version, playlist: Playlist) -> Playlist:
        """Add ``version`` to ``playlist`` without replacing existing members.

        Uses ShotGrid's ``multi_entity_update_modes={"playlists": "add"}``
        so other versions already on the playlist are preserved.

        Returns:
            The refreshed :class:`Playlist`.

        Raises:
            ShotGridWriteError: ShotGrid rejected the link.
        """
        raise NotImplementedError("Phase 0 skeleton")

    # ---- refresh -----------------------------------------------------------

    def reload(
        self,
        entity: Asset
        | Shot
        | Sequence
        | Environment
        | User
        | Task
        | Version
        | Playlist,
    ) -> Any:
        """Re-fetch ``entity`` from ShotGrid and return the fresh copy.

        Used by write verbs and by partial-entity lazy fetch. Safe for callers
        to use directly after out-of-band writes (e.g. another tool changed
        the asset while this tool held a stale reference).

        The return type matches the argument type at runtime. The declared
        return is ``Any`` because static type narrowing across entity unions
        is noisier than it is worth at the call site.

        Raises:
            ShotGridNotFound: ``entity.id`` no longer exists in ShotGrid.
        """
        raise NotImplementedError("Phase 0 skeleton")

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
    """Raise ``TypeError`` unless exactly one kwarg is non-``None``.

    Used by every ``get_*`` method to enforce the unique-selector contract
    at the call site — before any ShotGrid traffic, and before the Phase 0
    ``NotImplementedError``. This keeps the contract tests meaningful while
    the bodies are still stubs.
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
