"""Where a path a Windows machine names is on the Linux machines."""

from __future__ import annotations

import getpass
import re
from pathlib import PurePath, PurePosixPath

SAMBA_HOST = "cs-samba.cs.byu.edu"
HOMES = "homes"
SHARE_ROOTS = {
    "cache": PurePosixPath("/cache"),
    "groups": PurePosixPath("/groups"),
    "job": PurePosixPath("/job"),
    "warehouse": PurePosixPath("/warehouse"),
}
DRIVE_SHARES = {"G": "groups", "H": HOMES, "I": "cache", "J": "job", "W": "warehouse"}
STUDENT_HOMES = PurePosixPath("/users/student")

_DRIVE = re.compile(r"([A-Za-z]):(?:/(.*))?")
_UNC = re.compile(r"//([^/]+)/([^/]+)(?:/(.*))?")


class NotOnShare(ValueError):
    """A Windows path outside Linux Land."""


def is_windows_path(path: str | PurePath) -> bool:
    """Whether `path` is a full Windows path."""
    text = _slashed(path)
    return bool(_DRIVE.fullmatch(text) or _UNC.fullmatch(text))


def linux_path(path: str | PurePath) -> PurePosixPath:
    """`path` translated to Linux.

    Raises:
        NotOnShare: `path` is on a drive or server with no production share.
    """
    text = _slashed(path)
    if drive := _DRIVE.fullmatch(text):
        letter, rest = drive.groups()
        share = DRIVE_SHARES.get(letter.upper())
    elif unc := _UNC.fullmatch(text):
        host, name, rest = unc.groups()
        same_host = host.casefold() in (SAMBA_HOST, SAMBA_HOST.split(".")[0])
        share = name.casefold() if same_host else None
    else:
        return PurePosixPath(text)
    if share == HOMES:
        root = home()
    elif share in SHARE_ROOTS:
        root = SHARE_ROOTS[share]
    else:
        raise NotOnShare(f"{path} is on no production share")
    return root.joinpath(*(rest or "").split("/"))


def home() -> PurePosixPath:
    user = getpass.getuser()
    return STUDENT_HOMES / user[0] / user


def _slashed(path: str | PurePath) -> str:
    return str(path).replace("\\", "/")
