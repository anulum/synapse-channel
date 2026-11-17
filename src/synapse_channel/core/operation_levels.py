# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the autonomy level of a scope, and the kind of an approver
"""Name the autonomy levels, decode a request to set one, and tell a person from a role.

A scope (:mod:`synapse_channel.core.operation_stops`) has a level from ``A1`` to
``A5``. A scope with no level record is at ``A1``, the owner's starting level.
An operation's level is the lowest level among the scopes that contain it, so a
wider scope held at ``A1`` keeps every narrower one at ``A1``.

An approver is a *person* when the hub's role grants allow it the role
``<its project>/person``; otherwise it is *automated*. The kind is fixed when
the approval is admitted and never taken from the approval's text. An approval
admitted before kinds were recorded counts as automated.

An automated approval whose journal sequence is below the operation's *level
line*, the latest level record of any scope that contains it, is stale: an
automated approval dies with a change of level. A person's approval does not.

This module is pure; the hub records the levels and the kinds.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.operation_stops import parse_scope

if TYPE_CHECKING:
    from synapse_channel.core.role_grants import RoleGrants

LEVELS = ("A1", "A2", "A3", "A4", "A5")
"""The autonomy levels, from the least autonomy to the most."""

DEFAULT_LEVEL = "A1"
"""The level of a scope that has no level record."""

KIND_PERSON = "person"
KIND_AUTOMATED = "automated"
APPROVER_KINDS = (KIND_PERSON, KIND_AUTOMATED)

CAUSE_BY_HAND = "by_hand"
"""The cause of a level set by hand by an identity holding ``operation-level``."""

LEVEL_RECORDED = "level_recorded"
ALREADY_AT_LEVEL = "already_at_level"
NOT_PERMITTED = "not_permitted"
RAISE_STOPPED = "raise_stopped"
LEVEL_ANSWERS = (LEVEL_RECORDED, ALREADY_AT_LEVEL, NOT_PERMITTED, RAISE_STOPPED)
"""Every answer to a request to set a level, each journalled."""

PERSON_ROLE = "person"
"""The role, within an approver's project, that makes the approver a person."""

_MAX_REASON = 512
_MAX_KEY = 256


class OperationLevelError(SynapseError, ValueError):
    """Raised when a request to set a level is malformed."""

    code = "operation_level"


@dataclass(frozen=True)
class LevelRequest:
    """One decoded ``operation_level`` frame."""

    scope: str
    level: str
    reason: str
    idem_key: str


def _text(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise OperationLevelError(f"{label} must be text of 1 to {limit} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise OperationLevelError(f"{label} cannot contain control characters")
    return value


def decode_level_request(data: Mapping[str, Any], effects: Collection[str]) -> LevelRequest:
    """Decode and validate one ``operation_level`` frame.

    Raises
    ------
    OperationLevelError
        If the scope, the level or a text field is missing or of the wrong form.
    """
    try:
        scope = parse_scope(data.get("scope"), effects)
    except ValueError as exc:
        raise OperationLevelError(str(exc)) from None
    level = data.get("level")
    if level not in LEVELS:
        raise OperationLevelError(f"level must be one of {list(LEVELS)}")
    return LevelRequest(
        scope=scope,
        level=str(level),
        reason=_text(data.get("reason"), "reason", _MAX_REASON),
        idem_key=_text(data.get("idem_key"), "idem_key", _MAX_KEY),
    )


def rank(level: str) -> int:
    """Return the place of ``level`` in :data:`LEVELS`; an unknown level ranks lowest."""
    return LEVELS.index(level) if level in LEVELS else 0


def lowest(levels: Iterable[str]) -> str:
    """Return the lowest of ``levels``, or :data:`DEFAULT_LEVEL` when there are none."""
    return min(levels, key=rank, default=DEFAULT_LEVEL)


def approver_kind(grants: RoleGrants | None, approver: str) -> str:
    """Return ``person`` when the role grants allow ``approver`` its project's person role."""
    project = approver.split("/", 1)[0]
    if grants is not None and grants.may_claim(approver, f"{project}/{PERSON_ROLE}"):
        return KIND_PERSON
    return KIND_AUTOMATED
