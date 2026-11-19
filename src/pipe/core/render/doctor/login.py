"""The doctor's Tractor login: a saved session first, the password only when it must.

Tractor gives each login a session id, which only the computer that logged in
may use. The doctor keeps each computer's id in its own session file, readable
only by its user, and goes on using it for as long as the engine accepts it,
so the password is sent only when the session has ended, as when the engine
restarts. The password then comes from a password file,
also readable only by its user, which `python -m pipe.core.render.doctor login`
writes.

A refused password is never tried again until the password file changes, so
a password changed elsewhere can't lock the account by being retried every
round. The doctor never logs out, as that would end the session it saves.
"""

from __future__ import annotations

import getpass
import logging
import os
import stat
from collections.abc import Callable
from typing import Any

from pipe.core.render import farm
from pipe.core.render.doctor.records import Paths
from pipe.core.render.farm import TRACTOR_ENGINE, FarmError, LoginRefused

log = logging.getLogger(__name__)


class NoLogin(FarmError):
    """There is no session to reuse and no password to log in with."""


class Connection:
    """A logged-in Tractor client, which logs in again if its session ends."""

    def __init__(self, client: Any, how: str, log_in: Callable[[], None]) -> None:
        # Tractor's EngineClient, which ships untyped with RenderMan.
        self._client = client
        # How it logged in: "session" or "password", for the doctor's status.
        self.how = how
        self._log_in = log_in

    def retry_task(self, jid: int, tid: int) -> None:
        self._run(
            lambda: self._client.retryTask(jid, tid), f"retry task {tid} of job {jid}"
        )

    def set_service(self, jid: int, cid: int, service: str) -> None:
        self._run(
            lambda: self._client.setCommandAttribute(jid, cid, "service", service),
            f"change the service key of command {cid} of job {jid}",
        )

    def nimby(self, machine: str, address: str) -> None:
        """Take `machine` off the farm: Tractor sends it no new work."""
        self._run(
            lambda: self._client.nimbyBlade(machine, address),
            f"take {machine} off the farm",
        )

    def unnimby(self, machine: str, address: str) -> None:
        """Put `machine` back on the farm."""
        self._run(
            lambda: self._client.unnimbyBlade(machine, address),
            f"put {machine} back on the farm",
        )

    def _run(self, operation: Callable[[], None], doing: str) -> None:
        errors = _client_errors()
        try:
            operation()
            return
        except errors.TransactionError as exc:
            # The engine may have ended the session since it was checked. If it
            # did, log in again and try once more; if not, Tractor refused it.
            if self._client.canReuseSession():
                raise FarmError(f"Tractor refused to {doing}: {exc}") from exc
            log.info("The Tractor session ended; logging in again to %s", doing)
        except errors.EngineClientError as exc:
            raise FarmError(f"Could not {doing}: {exc}") from exc
        self._log_in()
        self.how = "password"
        try:
            operation()
        except errors.EngineClientError as exc:
            raise FarmError(f"Tractor refused to {doing}: {exc}") from exc


def connect(
    paths: Paths,
    user: str | None = None,
    password: Callable[[], str | None] | None = None,
) -> Connection:
    """Log in to Tractor as `user`, the current one by default, reusing the
    saved session if the engine still accepts it.

    The password comes from the password file, or else from `password`, such
    as the window's keyring.

    Raises:
        NoLogin: No session to reuse, and no password to log in with.
        LoginRefused: Tractor refused the password.
        FarmError: The engine can't be reached.
    """
    user = user or getpass.getuser()
    client = _client(paths, user)
    errors = _client_errors()

    def log_in() -> None:
        secret, source = _password(paths), "password file"
        if secret is None and password is not None:
            secret, source = password(), "keyring"
        if secret is None:
            raise NoLogin(
                f"The doctor has no Tractor login. Run `python -m pipe.core.render.doctor login` "
                f"to save one for {user}."
            )
        if source == "password file" and _refused(paths):
            raise LoginRefused(
                "Tractor refused the doctor's saved password, so it won't try it again. "
                "Run `python -m pipe.core.render.doctor login` to save the new one."
            )
        client.setParam(password=secret, newSession=True)
        try:
            client.open()
        except errors.OpenConnError as exc:
            if "Unable to log in" in str(exc):
                if source == "password file":
                    _mark_refused(paths)
                raise LoginRefused(f"Tractor refused the password for {user}.") from exc
            raise FarmError(
                f"Could not reach Tractor at {TRACTOR_ENGINE} ({exc})."
            ) from exc
        except errors.EngineClientError as exc:
            raise FarmError(f"Could not log in to Tractor: {exc}") from exc
        finally:
            # Only the session is kept from here on.
            client.setParam(newSession=False)
        paths.refused.unlink(missing_ok=True)
        log.info("Logged in to Tractor as %s with the %s", user, source)

    try:
        reusable = client.canReuseSession()
    except errors.EngineClientError as exc:
        raise FarmError(
            f"Could not reach Tractor at {TRACTOR_ENGINE} ({exc})."
        ) from exc
    if reusable:
        return Connection(client, "session", log_in)
    log_in()
    return Connection(client, "password", log_in)


def save_password(paths: Paths, password: str) -> None:
    """Keep `password` in the password file, readable only by this user."""
    paths.config.mkdir(parents=True, exist_ok=True)
    paths.config.chmod(0o700)
    temporary = paths.password.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(password + "\n")
    temporary.replace(paths.password)
    # A new password may be tried, even if the last was refused.
    paths.refused.unlink(missing_ok=True)


def forget_session(paths: Paths) -> None:
    """Drop the saved session, so the next login uses the password."""
    paths.session.unlink(missing_ok=True)


def _client(paths: Paths, user: str) -> Any:
    farm.use_tractor_api()
    import tractor.base.EngineClient as engine_client

    host, _, port = TRACTOR_ENGINE.partition(":")
    paths.config.mkdir(parents=True, exist_ok=True)
    # Its own client, not the query API's shared one, which the Farm tab logs
    # out after each retry: that would end the session the doctor keeps.
    return engine_client.EngineClient(
        hostname=host,
        port=int(port or 80),
        user=user,
        sessionFilename=str(paths.session),
    )


def _client_errors() -> Any:
    farm.use_tractor_api()
    import tractor.base.EngineClient as engine_client

    return engine_client


def _password(paths: Paths) -> str | None:
    """The saved password, or None when there is none or it is unsafe to use."""
    try:
        info = paths.password.stat()
    except FileNotFoundError:
        return None
    if info.st_uid != os.getuid() or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise FarmError(
            f"The doctor's password file, {paths.password}, can be read by others, so "
            f"it wasn't used. Run `chmod 600 {paths.password}`, or save it again."
        )
    # Only the newline the file ends with: a password may end in spaces.
    return paths.password.read_text(encoding="utf-8").removesuffix("\n") or None


def _refused(paths: Paths) -> bool:
    """Tractor refused the password file as it is now."""
    try:
        refused = paths.refused.read_text(encoding="utf-8").strip()
        return refused == str(paths.password.stat().st_mtime_ns)
    except OSError:
        return False


def _mark_refused(paths: Paths) -> None:
    paths.refused.write_text(str(paths.password.stat().st_mtime_ns), encoding="utf-8")
