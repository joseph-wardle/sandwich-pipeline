"""The Tractor login that retrying and archiving need"""

from __future__ import annotations

import getpass
import logging
import shutil
import subprocess

from Qt import QtCore, QtWidgets

SECRET_TOOL = "secret-tool"
_SERVICE = ("service", "sandwich-render-network-tractor")
_LABEL = "Tractor login (Sandwich Renderer)"
_SETTINGS = ("SandwichKwonDo", "RenderNetwork")
_USER_KEY = "tractor/user"
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


class LoginDialog(QtWidgets.QDialog):
    """Asks for the Tractor login that retrying and archiving need."""

    def __init__(
        self, parent: QtWidgets.QWidget, owners: list[str], doing: str
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tractor Login")
        self._user = QtWidgets.QLineEdit(remembered_user() or getpass.getuser())
        self._remember = QtWidgets.QCheckBox("Remember for this user")
        self._remember.setChecked(True)
        self._remember.setToolTip(
            "Keep the password in your desktop keyring, encrypted and unlocked by "
            "your login. Forget Login on the Farm tab removes it."
        )
        self._password = QtWidgets.QLineEdit()
        self._password.setEchoMode(QtWidgets.QLineEdit.Password)
        note = QtWidgets.QLabel(
            f"{doing} needs the Tractor login of the jobs' owner ({', '.join(owners)}) "
            "or of a Tractor administrator."
        )
        note.setWordWrap(True)
        form = QtWidgets.QFormLayout()
        form.addRow("User", self._user)
        form.addRow("Password", self._password)
        form.addRow("", self._remember)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._password.setFocus()

    def login(self) -> tuple[str, str]:
        return self._user.text().strip(), self._password.text()

    def remember(self) -> bool:
        return self._remember.isChecked()


class TractorLogin(QtCore.QObject):
    """The Tractor login for retrying and archiving, kept while the window is
    open. `changed` follows a login given, forgotten or refused; `note` says
    something the artist should know about it."""

    changed = QtCore.Signal()
    note = QtCore.Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._login: tuple[str, str] | None = None

    def current(
        self, parent: QtWidgets.QWidget, owners: list[str], doing: str
    ) -> tuple[str, str] | None:
        """The login to use: this session's, else the remembered one, else asked
        for; None if the artist cancels."""
        if self._login is None:
            # Read only when needed: the keyring may ask to be unlocked.
            self._login = load()
        return self._login or self._ask(parent, owners, doing)

    def user(self) -> str | None:
        return self._login[0] if self._login else remembered_user()

    def forget(self) -> None:
        self._login = None
        forget()
        self.changed.emit()

    def refused(self) -> None:
        """Ask again next time rather than repeat a wrong password, which also
        drops a remembered one, such as after a password change."""
        self.forget()

    def _ask(
        self, parent: QtWidgets.QWidget, owners: list[str], doing: str
    ) -> tuple[str, str] | None:
        dialog = LoginDialog(parent, owners, doing)
        if not dialog.exec_():
            return None
        self._login = dialog.login()
        if dialog.remember() and not save(*self._login):
            self.note.emit(
                "No keyring on this computer, so the login lasts until the window closes"
            )
        self.changed.emit()
        return self._login


_SHARED: TractorLogin | None = None


def shared_login() -> TractorLogin:
    """The window's one Tractor login, shared by every tab that needs it."""
    global _SHARED
    if _SHARED is None:
        _SHARED = TractorLogin()
    return _SHARED


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
