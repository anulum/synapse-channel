# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — mailbox keys of roles and the role/personal split of a directed chat
"""Name a role's own mailbox and decide which mailboxes a directed chat is pending in.

A role (``<project>/<role>``) outlives the sessions that hold it. Mail sent to a
role is pending once, in the role's mailbox ``role:<project>/<role>``, and any
current holder that acknowledges it advances the role's watermark. A holder's
personal mailbox keeps the mail addressed to its name or its project.

The two clauses split :func:`~synapse_channel.core.protocol.is_recipient`: the
personal clause matches a target part against the name or its bare project, the
role clause matches it against the role. Their union is exactly what
``is_recipient`` accepts for a name with roles, so no directed chat leaves every
mailbox.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from typing import Any

from synapse_channel.core.dead_letters import is_directed_target
from synapse_channel.core.protocol import is_recipient

ROLE_MAILBOX_PREFIX = "role:"
"""Prefix of a role's mailbox key; reserved, so no connection may use it as a name."""


def role_mailbox_key(role: str) -> str:
    """Return the mailbox key of ``role``.

    Parameters
    ----------
    role : str
        A full ``<project>/<role>`` name.

    Returns
    -------
    str
        ``role:<project>/<role>``.
    """
    return f"{ROLE_MAILBOX_PREFIX}{role.strip()}"


def is_role_mailbox_key(name: str) -> bool:
    """Return whether ``name`` carries the reserved role-mailbox prefix (any case)."""
    return name.strip().casefold().startswith(ROLE_MAILBOX_PREFIX)


def mailbox_role(key: str) -> str | None:
    """Return the role a mailbox key names, or ``None`` for a personal mailbox.

    Parameters
    ----------
    key : str
        A mailbox identity from the pending projection or the journal.

    Returns
    -------
    str or None
        The ``<project>/<role>`` part of a nonblank role key, else ``None``.
    """
    cleaned = key.strip()
    if not is_role_mailbox_key(cleaned):
        return None
    role = cleaned[len(ROLE_MAILBOX_PREFIX) :].strip()
    return role or None


def mailbox_project(key: str) -> str:
    """Return the project a mailbox belongs to.

    A personal mailbox belongs to the part of its name before the first ``/`` (the
    whole name when there is none); a role mailbox belongs to its role's project.
    """
    subject = mailbox_role(key) or key.strip()
    return subject.split("/", 1)[0]


def addresses_role(target: str, role: str) -> bool:
    """Return whether a directed ``target`` reaches ``role`` through the role clause.

    A part matches when the role matches it as a case-sensitive glob, exactly as
    :func:`~synapse_channel.core.protocol.is_recipient` matches a bound role. A
    broadcast or a ``*`` audience is not directed and never matches.
    """
    if not role or not is_directed_target(target):
        return False
    return any(
        fnmatch.fnmatchcase(role, part)
        for part in (raw.strip() for raw in target.split(","))
        if part
    )


def pending_in_mailbox(payload: Mapping[str, Any], key: str) -> bool:
    """Return whether one journalled chat is pending in the mailbox ``key``.

    The chat must be directed, off any private channel, and not sent by the
    mailbox's own name. A role key applies the role clause; any other key applies
    the personal clause (the name or its bare project).

    Parameters
    ----------
    payload : Mapping[str, Any]
        The chat event's payload.
    key : str
        A personal name or a ``role:`` key.

    Returns
    -------
    bool
        ``True`` when the chat waits in that mailbox until its watermark passes it.
    """
    if payload.get("channel"):
        return False
    target = str(payload.get("target") or "all")
    role = mailbox_role(key)
    if role is not None:
        return addresses_role(target, role)
    sender = str(payload.get("sender") or "")
    return sender != key and is_directed_target(target) and is_recipient(target, key)
