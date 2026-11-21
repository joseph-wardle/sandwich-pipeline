"""The artist's Tractor login, remembered between sessions in the desktop keyring.

The password goes to the Secret Service (KWallet or GNOME Keyring), which
encrypts it and unlocks it with the artist's desktop login, through
`secret-tool`. It never goes to a file. Where there is no keyring, nothing is
remembered and the window asks once a session, as before.

The user name, which isn't secret, is kept in the window's settings.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from Qt import QtCore

SECRET_TOOL = "secret-tool"
# Attributes the keyring entry is found by.
_SERVICE = ("service", "sandwich-render-network-tractor")
_LABEL = "Tractor login (Sandwich Renderer)"
_SETTINGS = ("SandwichKwonDo", "RenderNetwork")
_USER_KEY = "tractor/user"
# The keyring may ask the artist to unlock it first, so allow time for that.
_TIMEOUT_SECONDS = 60

log = logging.getLogger(__name__)


def load() -> tuple[str, str] | None:
    """The remembered (user, password), or None when there is none to use."""
    user = str(QtCore.QSettings(*_SETTINGS).value(_USER_KEY, "") or "")
    if not user:
        return None
    password = _secret_tool("lookup", *_SERVICE, "user", user)
    return (user, password) if password else None


def save(user: str, password: str) -> bool:
    """Remember the login; False when there is no keyring to keep it in."""
    if (
        _secret_tool(
            "store", f"--label={_LABEL}", *_SERVICE, "user", user, stdin=password
        )
        is None
    ):
        return False
    QtCore.QSettings(*_SETTINGS).setValue(_USER_KEY, user)
    return True


def forget() -> None:
    """Remove the remembered login, such as one Tractor refused."""
    settings = QtCore.QSettings(*_SETTINGS)
    if user := str(settings.value(_USER_KEY, "") or ""):
        _secret_tool("clear", *_SERVICE, "user", user)
    settings.remove(_USER_KEY)


def remembered_user() -> str:
    return str(QtCore.QSettings(*_SETTINGS).value(_USER_KEY, "") or "")


def _secret_tool(*args: str, stdin: str | None = None) -> str | None:
    """`secret-tool`'s output, or None when it isn't there or fails."""
    if shutil.which(SECRET_TOOL) is None:
        log.info(
            "No %s, so the Tractor login is kept for this session only", SECRET_TOOL
        )
        return None
    try:
        done = subprocess.run(
            [SECRET_TOOL, *args],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        log.warning("%s %s failed", SECRET_TOOL, args[0], exc_info=True)
        return None
    if done.returncode != 0:
        # Also how lookup says it has nothing; only a store failure matters.
        if args[0] != "lookup":
            log.warning("%s %s failed: %s", SECRET_TOOL, args[0], done.stderr.strip())
        return None
    return done.stdout
