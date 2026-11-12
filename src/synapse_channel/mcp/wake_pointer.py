# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authority-bound wake pointers
"""Describe coordination notifications without copying native message bodies."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from synapse_channel.core.errors import SynapseError

WakeSource = Literal["messages", "tasks", "handoffs"]
WakePosition = int | str
WAKE_SOURCES = ("messages", "tasks", "handoffs")
MAX_WAKE_INTEGER = 9223372036854775807
MAX_WAKE_BODY_BYTES = 16384
WAKE_LEASE_SECONDS = 10
WAKE_RETRY_SECONDS = (1, 5, 30)
WAKE_MAX_ATTEMPTS = len(WAKE_RETRY_SECONDS) + 1


class WakeLedgerError(SynapseError, RuntimeError):
    """Refuse unavailable or inconsistent wake storage without exposing its content."""

    code = "mcp_wake_ledger"


class WakePolicyConflict(WakeLedgerError):
    """Refuse another policy's access to an already bound wake root."""

    code = "mcp_wake_policy_conflict"


def validate_policy_name(name: str) -> None:
    """Require an optional portable basename without directory or stream selectors."""
    if (
        type(name) is not str
        or len(name) > 255
        or name in (".", "..")
        or any(character in name for character in ("/", "\\", ":", "\x00"))
    ):
        raise WakeLedgerError("invalid wake policy name")


def validate_position(source: WakeSource, position: WakePosition) -> None:
    """Require a journal sequence or a digest of an exact-recipient projection."""
    valid = (
        type(position) is int and 0 <= position <= MAX_WAKE_INTEGER
        if source == "messages"
        else type(position) is str and re.fullmatch(r"[0-9a-f]{64}", position) is not None
    )
    if source not in WAKE_SOURCES or not valid:
        raise WakeLedgerError("invalid wake source position")


def wake_scope_key(subject: str, project: str, seat: str, binding: str) -> str:
    """Derive the storage filename from admitted authority independently of a hub binding."""
    raw = json.dumps([subject, project, seat, binding], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class WakeScope:
    """Bind local notifications to one provisioned subject, seat and native hub.

    The binding is a digest of operator configuration, never a token or a key.
    Hub identity is checked inside the ledger; a new hub cannot silently reuse
    an old scan position. This value does not itself grant native authority.
    """

    subject: str
    project: str
    seat: str
    binding: str
    hub_id: str
    policy_name: str = ""

    def __post_init__(self) -> None:
        """Reject ambiguous namespaces and unbounded identity values."""
        validate_policy_name(self.policy_name)
        values = (self.subject, self.project, self.seat, self.binding, self.hub_id)
        if (
            any(type(value) is not str or not value or len(value) > 256 for value in values)
            or "/" in self.project
            or self.project.strip() != self.project
            or not self.seat.startswith(self.project + "/")
            or self.seat == self.project + "/"
            or re.fullmatch(r"[0-9a-f]{64}", self.binding) is None
        ):
            raise WakeLedgerError("invalid wake identity binding")

    @property
    def key(self) -> str:
        """Return a filename-safe binding that remains stable across hub restarts."""
        return wake_scope_key(self.subject, self.project, self.seat, self.binding)

    def body(self, delivery_id: str, revision: int, sources: int) -> bytes:
        """Build one bounded hint; a wake never attests read or business completion."""
        if (
            type(delivery_id) is not str
            or re.fullmatch(r"msg_[0-9a-f]{32}", delivery_id) is None
            or type(revision) is not int
            or not 1 <= revision <= MAX_WAKE_INTEGER
            or type(sources) is not int
            or not 1 <= sources <= 7
        ):
            raise WakeLedgerError("invalid wake delivery pointer")
        raw = json.dumps(
            {
                "version": "synapse.wake.v1",
                "id": delivery_id,
                "project": self.project,
                "seat": self.seat,
                "hub_id": self.hub_id,
                "revision": revision,
                "changes": [
                    source for index, source in enumerate(WAKE_SOURCES) if sources & (1 << index)
                ],
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return raw


@dataclass(frozen=True)
class WakeAttempt:
    """Hold immutable bytes and the lifetime attempt fence, never reset by redrive."""

    delivery_id: str
    revision: int
    body: bytes
    attempt: int
    deadline: int
