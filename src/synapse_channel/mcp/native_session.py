# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native-session-bound MCP stdio identities
"""Opt in to stable routing names supplied by a supported native MCP client.

A native UUID identifies a local session, not an authenticated hub principal.
Normal hub credentials and authorization still apply to every derived name.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import UUID

from synapse_channel.advisory_lock import locked


class NativeSessionError(ValueError):
    """Refuse a native session whose identifier, prefix or storage is not usable."""


_PREFIX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}/[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")


def _native_uuid(vendor: str) -> str:
    """Read the supported producer's canonical UUID without echoing bad input."""
    if vendor != "grok":
        raise NativeSessionError("synapse mcp: unsupported native client")
    raw = os.environ.get("GROK_SESSION_ID", "")
    try:
        identifier = UUID(raw)
    except ValueError as exc:
        raise NativeSessionError(
            "synapse mcp: Grok must supply a canonical GROK_SESSION_ID"
        ) from exc
    if str(identifier) != raw or identifier.int == 0:
        raise NativeSessionError("synapse mcp: Grok must supply a canonical GROK_SESSION_ID")
    return raw


def _session_name(prefix: str, identifier: str) -> str:
    """Append the complete UUID to a bounded explicit project/client prefix."""
    if _PREFIX.fullmatch(prefix) is None:
        raise NativeSessionError(
            "synapse mcp: native client requires a bounded project/client prefix"
        )
    return f"{prefix}-{identifier}"


@contextmanager
def native_stdio_session(
    name: str, roles: tuple[str, ...], vendor: str | None
) -> Iterator[tuple[str, tuple[str, ...]]]:
    """Hold derived primary and role names until their native bridge exits.

    Native mode requires the client's UUID and never falls back to a shared
    name. Sorted nonblocking OS locks refuse a duplicate on this host while
    the original bridge remains alive; process exit releases them. Resuming
    the same native UUID derives the same routing names and inbox cursor.
    All participants must use the same owner-local SYN_HOME. Remote ownership
    remains governed by the hub, and no takeover is requested here.
    """
    if vendor is None:
        yield name, roles
        return
    identifier = _native_uuid(vendor)
    primary = _session_name(name, identifier)
    aliases = tuple(_session_name(role, identifier) for role in roles)
    home = Path(os.environ.get("SYN_HOME", str(Path.home() / "synapse"))).expanduser()
    if not home.is_absolute():
        raise NativeSessionError("synapse mcp: native sessions require an absolute SYN_HOME")
    with ExitStack() as stack:
        for identity in sorted(set((primary, *aliases))):
            path = (
                home
                / "mcp-native-sessions"
                / (hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".lock")
            )
            try:
                acquired = stack.enter_context(
                    locked(path, blocking=False, purpose="native MCP session lock directory")
                )
            except OSError as exc:
                raise RuntimeError("synapse mcp: native session lock is unavailable") from exc
            if not acquired:
                raise RuntimeError("synapse mcp: this native session identity is already active")
        yield primary, aliases
