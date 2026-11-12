# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — OpenCode connector authentication boundary
"""Validate OpenCode endpoints and load Basic-auth secrets without argv exposure."""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from synapse_channel.core.errors import SynapseError

MAX_PASSWORD_BYTES = 8_192
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class OpenCodeAuthError(SynapseError, ValueError):
    """An OpenCode endpoint or password file violates the connector policy."""

    code = "opencode_auth"


def validate_endpoint(endpoint: str, *, allow_insecure_http: bool = False) -> str:
    """Return a normalized HTTP(S) endpoint, refusing remote cleartext by default."""
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise OpenCodeAuthError("OpenCode endpoint must be an absolute HTTP(S) URL.")
    if parsed.username is not None or parsed.password is not None:
        raise OpenCodeAuthError("OpenCode endpoint must not embed credentials.")
    if parsed.query or parsed.fragment:
        raise OpenCodeAuthError("OpenCode endpoint must not contain a query or fragment.")
    try:
        _ = parsed.port  # urlsplit validates the range only when asked
    except ValueError as exc:
        raise OpenCodeAuthError(
            "OpenCode endpoint port must be an integer from 0 to 65535."
        ) from exc
    if (
        parsed.scheme == "http"
        and parsed.hostname.lower() not in LOOPBACK_HOSTS
        and not allow_insecure_http
    ):
        raise OpenCodeAuthError(
            "Remote OpenCode HTTP is refused; use HTTPS or explicitly allow insecure HTTP."
        )
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def load_password_file(path: str | Path) -> str:
    """Read one owner-only regular UTF-8 password file without following a symlink."""
    resolved = Path(path).expanduser()
    before = resolved.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise OpenCodeAuthError("OpenCode password file must be a user-owned regular file.")
    if hasattr(os, "getuid"):
        if before.st_uid != os.getuid():
            raise OpenCodeAuthError("OpenCode password file must be a user-owned regular file.")
        if stat.S_IMODE(before.st_mode) & 0o077:
            raise OpenCodeAuthError(
                "OpenCode password file must not be accessible by group or others."
            )
    elif os.name == "nt":
        from synapse_channel.core.secure_path import SecurePathError, assert_owner_only_file_path

        try:
            assert_owner_only_file_path(resolved, purpose="OpenCode password file")
        except SecurePathError as exc:
            raise OpenCodeAuthError(
                "OpenCode password file must be a user-owned regular file."
            ) from exc
        before = resolved.lstat()
    else:
        raise OpenCodeAuthError("OpenCode password file must be a user-owned regular file.")
    # O_BINARY: on Windows a descriptor opened without it translates CRLF and
    # stops at Ctrl-Z, so the password would not be the file's bytes.
    descriptor = os.open(
        resolved,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
    )
    try:
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise OpenCodeAuthError("OpenCode password file changed while opening.")
        data = os.read(descriptor, MAX_PASSWORD_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > MAX_PASSWORD_BYTES:
        raise OpenCodeAuthError("OpenCode password file exceeds the bounded size limit.")
    try:
        password = data.decode("utf-8", errors="strict").rstrip("\r\n")
    except UnicodeDecodeError as exc:
        raise OpenCodeAuthError("OpenCode password file is not valid UTF-8.") from exc
    if not password or "\0" in password:
        raise OpenCodeAuthError("OpenCode password file is empty or contains NUL.")
    return password


def basic_authorization(username: str, password: str) -> str:
    """Return an RFC 7617 Basic authorization value for validated credentials."""
    if not username or ":" in username or any(ord(char) < 0x20 for char in username):
        raise OpenCodeAuthError("OpenCode username is empty or contains invalid characters.")
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return f"Basic {encoded}"
