# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — which target parts reach which mailbox
"""Decide whether one stored target part reaches one mailbox.

The rules are the ones the chat scan used. A personal mailbox matches its name
or its bare project. A role mailbox matches only its role. A glob part matches
with ``fnmatch``. A star audience is not directed, so it never becomes a hit.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any

from synapse_channel.core.dead_letters import is_directed_target
from synapse_channel.core.hub_address import is_hub_qualified_name
from synapse_channel.core.role_mailbox import mailbox_role


def counts_at_all(payload: dict[str, Any]) -> bool:
    """Return whether a chat payload can contribute a mailbox hit."""
    if payload.get("channel"):
        return False
    return is_directed_target(str(payload.get("target") or "all"))


def parts(target: str) -> tuple[str, ...]:
    """Return the non-blank comma-separated parts of a target."""
    return tuple(part for raw in target.split(",") if (part := raw.strip()))


def identity_addresses(payload: dict[str, Any]) -> tuple[str, ...]:
    """Return exact local parts a chat introduces as mailbox names."""
    if not counts_at_all(payload):
        return ()
    return tuple(
        address
        for address in parts(str(payload.get("target") or "all"))
        if not is_glob(address) and not is_hub_qualified_name(address)
    )


def exact_addresses(identity: str) -> tuple[str, ...]:
    """Return the exact hit addresses that reach ``identity``."""
    role = mailbox_role(identity)
    if role is not None:
        return (role,)
    project = identity.split("/", 1)[0]
    if project == identity:
        return (identity,)
    return (identity, project)


def is_glob(address: str) -> bool:
    """Return whether a target part is a glob the scan still treated as directed."""
    return "?" in address or "[" in address or "*" in address


def any_reaches(addresses: tuple[str, ...], identity: str) -> bool:
    """Return whether any stored part reaches ``identity``."""
    return any(reaches(address, identity) for address in addresses)


def reaches(address: str, identity: str) -> bool:
    """Return whether one stored target part reaches one mailbox, as the scan did."""
    role = mailbox_role(identity)
    if role is not None:
        return fnmatchcase(role, address)
    project = identity.split("/", 1)[0]
    return fnmatchcase(identity, address) or fnmatchcase(project, address)
