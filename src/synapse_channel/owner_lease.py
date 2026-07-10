# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — persist a name's hub ownership-lease token across reconnects
"""Persist the hub's ownership-lease token so a reconnect re-takes its own name.

The hub grants an opaque ``owner_lease`` token to the first opt-in claimant of a
name and thereafter admits a claim on that name only when it presents the token
(see :mod:`synapse_channel.core.name_ownership`). The lease outlives the socket —
that is its whole point — so the client half must outlive the process: a waiter
re-arms as a fresh process after every wake, and without a durable token the
re-arm would be refused as a stranger on its own name until the hub's offline
window lapsed. This module keeps the token in a tiny per-identity file, the same
posture as the mailbox ``since_seq`` cursor it sits beside.

The token is a bearer credential, so the file is written atomically with
owner-only permissions (``0o600``). A missing, corrupt, or unreadable file reads
as an empty token: the claim then simply presents nothing, and either the hub
never leased the name (first claim — a fresh lease is granted) or the refusal
tells the operator the name is owned. A lost token degrades to a bounded
wait-out, never a crash.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

LEASE_FILE_MODE = 0o600


def lease_path(identity: str, *, base: Path | None = None) -> Path:
    """Return the lease-token file path for ``identity``, one file per identity.

    The identity is URL-quoted with no safe characters, so a ``<project>/<id>``
    name becomes a single flat filename (its slash is escaped) rather than a
    nested directory a stray identity could climb out of — the same containment
    :func:`synapse_channel.mailbox_cursor.cursor_path` applies.

    Parameters
    ----------
    identity : str
        The connection name whose ownership lease this is (for a waiter, its
        ``-rx`` connect name; for a one-shot verb, the bare identity it binds).
    base : pathlib.Path or None, optional
        Directory to hold lease files. ``None`` uses ``~/synapse/owner-lease``,
        a sibling of the mailbox-cursor directory and the hub's durable feed.

    Returns
    -------
    pathlib.Path
        The path to this identity's lease-token file.
    """
    directory = base if base is not None else Path.home() / "synapse" / "owner-lease"
    return directory / quote(identity, safe="")


def load_lease(path: str | Path) -> str:
    """Read a persisted lease token, returning ``""`` when absent or unreadable.

    Parameters
    ----------
    path : str or pathlib.Path
        File holding the single-line lease token.

    Returns
    -------
    str
        The stored token, or the empty string on a missing, empty, or
        unreadable file — the claim then presents no token, which is correct
        for a name that was never leased and an actionable refusal for one
        that was.
    """
    marker = Path(path)
    if not marker.exists():
        return ""
    try:
        return marker.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_lease(path: str | Path, token: str) -> None:
    """Persist a lease token atomically with owner-only permissions.

    The token is written to a temporary file in the destination directory
    (created ``0o600`` by :func:`tempfile.mkstemp`), flushed to disk, then
    renamed onto the lease path with :func:`os.replace`, so a crash or a
    concurrent reader never observes a half-written token — a torn token would
    present as a stranger and lock the identity out of its own name until the
    hub's offline window lapsed. An empty ``token`` removes the file instead,
    so a deliberately cleared lease does not linger as an empty credential.

    Parameters
    ----------
    path : str or pathlib.Path
        Destination lease-token file.
    token : str
        The lease token to store; empty removes any stored token.
    """
    marker = Path(path)
    if not token:
        marker.unlink(missing_ok=True)
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=marker.parent, prefix=f"{marker.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, marker)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def lease_agent_kwargs(path: Path | None) -> dict[str, Any]:
    """Return the client keyword triple wiring lease persistence to ``path``.

    A connection that participates in ownership leasing needs three
    :class:`~synapse_channel.client.agent.SynapseAgent` arguments that always
    travel together: opt in (``request_lease``), present what is stored
    (``owner_lease``), and persist what is granted (``on_lease_granted``).
    This builds the triple so every call site stays a one-liner and cannot
    wire half of it.

    Parameters
    ----------
    path : pathlib.Path or None
        The identity's lease-token file, from :func:`lease_path`. ``None``
        returns an empty mapping — the connection does not participate in
        leasing at all, which keeps tests and pre-lease callers byte-identical
        to today's behaviour.

    Returns
    -------
    dict[str, Any]
        Keyword arguments to splat into the agent factory.
    """
    if path is None:
        return {}
    return {
        "request_lease": True,
        "owner_lease": load_lease(path),
        "on_lease_granted": lambda token: save_lease(path, token),
    }
