# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the scopes a stop covers and the requests to stop and resume them
"""Name the scopes a stop can cover, decode the requests, and state the answers.

A stop is recorded for one scope and covers every operation the scope contains.
In this version a scope is one of:

* ``installation``: every operation;
* ``project:<namespace>``: the operations whose declarer is in that project;
* ``effect:<kind>``: the operations declared with that effect, such as
  ``effect:git_publication``;
* ``operation:<operation_id>``: that operation.

An operation is stopped when any scope that contains it is stopped, and a resume
lifts only the scope it names. While a scope is stopped no attempt of an
operation it contains is opened, and no approval of such an operation is
admitted. An approval older than the latest stop or resume of any scope that
contains the operation opens no attempt either; that latest record's journal
sequence is the operation's *stop line*.

This module is pure: it decodes the requests and names the scopes. Recording
the stops, reading the open attempts and answering belong to the hub.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.errors import SynapseError

SCOPE_INSTALLATION = "installation"
"""The scope that contains every operation."""

STOP_RECORDED = "stop_recorded"
ALREADY_STOPPED = "already_stopped"
STOP_ANSWERS = (STOP_RECORDED, ALREADY_STOPPED)
"""Every answer to a request to stop, each journalled. An enforcing hub's access
gate refuses a stop without ``operation-stop`` before the verb, unjournalled."""

RESUMED = "resumed"
NOT_STOPPED = "not_stopped"
NOT_PERMITTED = "not_permitted"
ACTOR_OF_SCOPE = "actor_of_scope"
RESUME_ANSWERS = (RESUMED, NOT_STOPPED, NOT_PERMITTED, ACTOR_OF_SCOPE)
"""Every answer to a request to resume, each journalled."""

CASE_ANOTHER_PERSON = "another_person"
CASE_ONLY_PERSON = "only_person"
RESUME_CASES = (CASE_ANOTHER_PERSON, CASE_ONLY_PERSON)
"""Who resumes, as the resumer states it: a person other than the stopper, or the only one."""

RESUMED_FROM_STOP_REQUESTED = "stop requested"
"""The state a resume lifts while no listed attempt has been sealed or read back."""

MAX_REASON = 512
"""Longest reason a stop or resume may give, in characters."""

_MAX_KEY = 256
_NAMESPACE = re.compile(r"[A-Za-z0-9._-]{1,128}")
_OPERATION_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


class OperationStopError(SynapseError, ValueError):
    """Raised when a request to stop or resume is malformed."""

    code = "operation_stop"


@dataclass(frozen=True)
class StopRequest:
    """One decoded ``operation_stop`` frame."""

    scope: str
    reason: str
    idem_key: str


@dataclass(frozen=True)
class ResumeRequest:
    """One decoded ``operation_resume`` frame; ``case`` is stated, not proven."""

    scope: str
    reason: str
    case: str
    idem_key: str


def _text(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise OperationStopError(f"{label} must be text of 1 to {limit} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise OperationStopError(f"{label} cannot contain control characters")
    return value


def parse_scope(value: object, effects: Collection[str]) -> str:
    """Return ``value`` as a scope, or raise when it names none.

    Parameters
    ----------
    value : object
        The scope as the request states it.
    effects : Collection[str]
        The effect kinds a declaration may carry.

    Returns
    -------
    str
        The scope, unchanged.

    Raises
    ------
    OperationStopError
        If the value is not one of the scope forms; it is not echoed.
    """
    if value == SCOPE_INSTALLATION:
        return SCOPE_INSTALLATION
    if isinstance(value, str):
        kind, _, name = value.partition(":")
        if kind == "project" and _NAMESPACE.fullmatch(name):
            return value
        if kind == "effect" and name in effects:
            return value
        if kind == "operation" and _OPERATION_ID.fullmatch(name):
            return value
    raise OperationStopError(
        "scope must be installation, project:<namespace>, effect:<kind> or operation:<id>"
    )


def decode_stop_request(data: Mapping[str, Any], effects: Collection[str]) -> StopRequest:
    """Decode and validate one ``operation_stop`` frame.

    Raises
    ------
    OperationStopError
        If a field is missing, of the wrong form or out of bounds.
    """
    return StopRequest(
        scope=parse_scope(data.get("scope"), effects),
        reason=_text(data.get("reason"), "reason", MAX_REASON),
        idem_key=_text(data.get("idem_key"), "idem_key", _MAX_KEY),
    )


def decode_resume_request(data: Mapping[str, Any], effects: Collection[str]) -> ResumeRequest:
    """Decode and validate one ``operation_resume`` frame.

    Raises
    ------
    OperationStopError
        If a field is missing, of the wrong form or out of bounds, or ``case``
        is not one of :data:`RESUME_CASES`.
    """
    case = data.get("case")
    if case not in RESUME_CASES:
        raise OperationStopError("case must be another_person or only_person")
    return ResumeRequest(
        scope=parse_scope(data.get("scope"), effects),
        reason=_text(data.get("reason"), "reason", MAX_REASON),
        case=str(case),
        idem_key=_text(data.get("idem_key"), "idem_key", _MAX_KEY),
    )


def scopes_of(operation_id: str, declarer: str | None, effect: str | None) -> tuple[str, ...]:
    """Return every scope that contains an operation, the narrowest first.

    Parameters
    ----------
    operation_id : str
        The operation.
    declarer, effect : str or None
        Its declarer and declared effect; ``None`` for an operation the hub
        has no declaration of, which only ``installation`` contains.

    Returns
    -------
    tuple of str
        ``operation:<id>``, then ``effect:<kind>`` and ``project:<namespace>``
        when known, then ``installation``.
    """
    if declarer is None:
        return (SCOPE_INSTALLATION,)
    scopes = [f"operation:{operation_id}"]
    if effect:
        scopes.append(f"effect:{effect}")
    scopes.append(f"project:{declarer.split('/', 1)[0]}")
    scopes.append(SCOPE_INSTALLATION)
    return tuple(scopes)
