# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the authorisation intent an operation declaration can carry
"""Validate, compose and digest the authorisation intent of one declared operation.

An intent is a fixed description of what an operation will do, written before
the effect so that an approval can name its exact digest. The declarer supplies
a *statement*: the state the effect starts from, the recovery class, a deadline,
the budget, the origins it may reach, the tool and the digest of its arguments,
the rule generation it was planned under and the assurance it requires. The hub
adds the declaration's own identifiers and the authenticated sender as
``actor``. The intent is the union of both; its canonical bytes are RFC 8785
JSON under the integer-only profile of :mod:`synapse_channel.core.aef_canonical`
and its digest is their SHA-256. Nothing time-dependent is added, so a reviewer
who holds the statement and the declaration recomputes the digest offline.

An intent grants nothing. It is data the hub validates and keeps beside the
declaration; whether an approval of its digest admits an effect is decided
elsewhere.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from synapse_channel.core.aef_canonical import (
    IJSON_MAX_INTEGER,
    AefCanonicalizationError,
    canonical_json,
)
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.http_authority import normalise_url_origin

INTENT_SCHEMA = "synapse.authorisation-intent.v0"
"""The one schema name this version accepts."""

MAX_INTENT_BYTES = 64 * 1024
"""Largest canonical intent, in bytes, that a declaration may carry."""

MAX_DEADLINE_AHEAD = 366 * 24 * 60 * 60
"""Furthest a deadline may lie after the hub's clock at the declaration, in seconds."""

MAX_CHANGED_FILES = 1000
"""Most paths one git publication's intent may list."""

MAX_EGRESS_ORIGINS = 16
"""Most origins one intent may allow its effect to reach."""

REFUSED_INTENT_INVALID = "intent_invalid"
REFUSED_INTENT_EXPIRED = "intent_expired"

HUB_FIELDS = frozenset({"actor", "task_id", "operation_id", "effect", "effect_target"})
"""Fields the hub adds to every intent; a statement that names one is refused."""

RECOVERY_CLASSES = frozenset({"reversible", "restorable", "one_way"})
ASSURANCE_LEVELS = frozenset({"cooperative", "controlled", "isolated"})
GIT_MODES = frozenset({"100644", "100755", "120000", "160000", "absent"})

_REQUIRED = frozenset(
    {
        "schema",
        "pre_state",
        "recovery_class",
        "deadline",
        "budget",
        "egress",
        "tool",
        "arguments_sha256",
        "rule_generation",
        "required_assurance",
    }
)
_OPTIONAL = frozenset({"changed_files", "tool_descriptor_sha256"})

_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_RESERVATION = re.compile(r"reservation:[A-Za-z0-9._:-]{1,128}")
_RULE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_PROSE_RULES = re.compile(r"prose-rules:(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z")
_MAX_TEXT = 128
_MAX_PATH = 512


class AuthorisationIntentError(SynapseError, ValueError):
    """Raised when an intent statement is malformed; rejected values are not echoed."""

    code = "authorisation_intent"


@dataclass(frozen=True)
class IntentStatement:
    """A validated statement: the part of an intent the declarer supplies.

    Attributes
    ----------
    document : Mapping[str, Any]
        The statement's fields, each already in its one accepted form.
    deadline : int
        The deadline in whole UNIX seconds, kept apart for the clock check.
    """

    document: Mapping[str, Any]
    deadline: int


@dataclass(frozen=True)
class AuthorisationIntent:
    """A composed intent with its canonical bytes and digest.

    Attributes
    ----------
    document : dict[str, Any]
        The statement together with the fields the hub added.
    canonical : bytes
        The RFC 8785 bytes of ``document``.
    sha256 : str
        Lower-case hex SHA-256 of ``canonical``; an approval names it.
    """

    document: dict[str, Any]
    canonical: bytes
    sha256: str


def _refuse(message: str) -> AuthorisationIntentError:
    return AuthorisationIntentError(f"authorisation intent: {message}")


def _text(value: object, label: str, limit: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise _refuse(f"{label} must be text of 1 to {limit} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise _refuse(f"{label} cannot contain control characters")
    return value


def _match(value: object, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise _refuse(f"{label} has the wrong form")
    return value


def _choice(value: object, label: str, choices: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise _refuse(f"{label} must be one of {sorted(choices)}")
    return value


def _pre_state(effect: str, value: object) -> str:
    """Return the pre-state in the form the declared effect starts from."""
    if effect == "file":
        if value == "absent":
            return "absent"
        return _match(value, "a file pre_state ('absent' or a content SHA-256)", _HEX64)
    if effect == "git_publication":
        return _match(value, "a git_publication pre_state (the base commit)", _HEX40)
    if effect == "deployment":
        return _text(value, "a deployment pre_state (the running revision)")
    if effect == "migration":
        return _match(value, "a migration pre_state (the schema SHA-256 before)", _HEX64)
    raise _refuse("the declared effect is unknown")


def _relative_path(value: object) -> str:
    path = _text(value, "a changed path", _MAX_PATH)
    parts = path.split("/")
    if path.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise _refuse("a changed path must be relative, with no empty, '.' or '..' part")
    return path


def _changed_files(value: object) -> list[dict[str, str]]:
    """Return the git publication's changed paths, each once and in order."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_CHANGED_FILES:
        raise _refuse(f"changed_files must list 1 to {MAX_CHANGED_FILES} paths")
    entries: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"path", "mode"}:
            raise _refuse("each changed_files entry names exactly a path and a mode")
        entries.append(
            {
                "path": _relative_path(item.get("path")),
                "mode": _choice(item.get("mode"), "a changed file's mode", GIT_MODES),
            }
        )
    paths = [entry["path"] for entry in entries]
    if any(earlier >= later for earlier, later in zip(paths, paths[1:], strict=False)):
        raise _refuse("changed_files must be sorted by path, each path once")
    return entries


def _deadline(value: object) -> int:
    if type(value) is not int or not 1 <= value <= IJSON_MAX_INTEGER:
        raise _refuse("deadline must be whole UNIX seconds")
    return value


def _budget(value: object) -> str:
    if value == "not_applicable":
        return "not_applicable"
    return _match(value, "budget ('not_applicable' or 'reservation:<id>')", _RESERVATION)


def _egress(value: object) -> list[str]:
    """Return the allowed origins, each canonical, sorted and once."""
    if not isinstance(value, list) or len(value) > MAX_EGRESS_ORIGINS:
        raise _refuse(f"egress must list 0 to {MAX_EGRESS_ORIGINS} origins")
    origins: list[str] = []
    for item in value:
        text = _text(item, "an egress origin", _MAX_PATH)
        try:
            canonical = normalise_url_origin(text)
        except ValueError:
            raise _refuse("an egress origin must be one HTTP(S) origin") from None
        if canonical != text:
            raise _refuse("an egress origin must be written in its canonical form")
        origins.append(text)
    if any(earlier >= later for earlier, later in zip(origins, origins[1:], strict=False)):
        raise _refuse("egress origins must be sorted, each once")
    return origins


def _tool(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"name", "version"}:
        raise _refuse("tool names exactly a name and a version")
    return {
        "name": _text(value.get("name"), "tool name"),
        "version": _text(value.get("version"), "tool version"),
    }


def _rule_generation(value: object) -> str:
    """Return a rule generation digest, or the prose rules read as of a UTC second."""
    if isinstance(value, str) and _RULE_DIGEST.fullmatch(value):
        return value
    match = _PROSE_RULES.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise _refuse("rule_generation must be 'sha256:<hex>' or 'prose-rules:<UTC time>Z'")
    try:
        datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise _refuse("rule_generation names a time that does not exist") from None
    return str(value)


def _check_keys(value: Mapping[Any, Any], effect: str) -> None:
    keys = set(value)
    named = sorted(key for key in keys & HUB_FIELDS)
    if named:
        raise _refuse(f"the hub fills {named}; a statement cannot name them")
    missing = _REQUIRED - keys
    if missing:
        raise _refuse(f"the statement lacks {sorted(missing)}")
    unknown = keys - _REQUIRED - _OPTIONAL
    if unknown:
        raise _refuse("the statement has fields this schema does not know")
    if ("changed_files" in keys) != (effect == "git_publication"):
        raise _refuse("changed_files is required for a git_publication and refused otherwise")


def parse_intent_statement(value: object, effect: str) -> IntentStatement:
    """Validate the statement a declarer supplies for a declaration of ``effect``.

    Parameters
    ----------
    value : object
        The decoded ``intent`` object of the declaration frame.
    effect : str
        The declaration's effect; it decides the pre-state's form and whether
        ``changed_files`` belongs to the statement.

    Returns
    -------
    IntentStatement
        The statement with every field in its accepted form.

    Raises
    ------
    AuthorisationIntentError
        If the statement is not an object, names a field the hub fills, lacks
        a field, has an unknown one, or any field has the wrong form.
    """
    if not isinstance(value, Mapping):
        raise _refuse("the intent must be a JSON object")
    _check_keys(value, effect)
    if value.get("schema") != INTENT_SCHEMA:
        raise _refuse(f"schema must be {INTENT_SCHEMA}")
    deadline = _deadline(value.get("deadline"))
    document: dict[str, Any] = {
        "schema": INTENT_SCHEMA,
        "pre_state": _pre_state(effect, value.get("pre_state")),
        "recovery_class": _choice(value.get("recovery_class"), "recovery_class", RECOVERY_CLASSES),
        "deadline": deadline,
        "budget": _budget(value.get("budget")),
        "egress": _egress(value.get("egress")),
        "tool": _tool(value.get("tool")),
        "arguments_sha256": _match(value.get("arguments_sha256"), "arguments_sha256", _HEX64),
        "rule_generation": _rule_generation(value.get("rule_generation")),
        "required_assurance": _choice(
            value.get("required_assurance"), "required_assurance", ASSURANCE_LEVELS
        ),
    }
    if "changed_files" in value:
        document["changed_files"] = _changed_files(value.get("changed_files"))
    if "tool_descriptor_sha256" in value:
        document["tool_descriptor_sha256"] = _match(
            value.get("tool_descriptor_sha256"), "tool_descriptor_sha256", _HEX64
        )
    return IntentStatement(document, deadline)


def intent_time_refusal(statement: IntentStatement, now: float) -> str | None:
    """Return why the hub's clock refuses the statement's deadline, or ``None``.

    Parameters
    ----------
    statement : IntentStatement
        A validated statement.
    now : float
        The hub's clock, in UNIX seconds, at the declaration.

    Returns
    -------
    str or None
        ``intent_expired`` when the deadline is not after ``now``,
        ``intent_invalid`` when it lies more than :data:`MAX_DEADLINE_AHEAD`
        seconds after it, otherwise ``None``.
    """
    if statement.deadline <= now:
        return REFUSED_INTENT_EXPIRED
    if statement.deadline > now + MAX_DEADLINE_AHEAD:
        return REFUSED_INTENT_INVALID
    return None


def compose_intent(
    statement: IntentStatement,
    *,
    task_id: str,
    operation_id: str,
    effect: str,
    target: Mapping[str, str],
    actor: str,
) -> AuthorisationIntent:
    """Add the hub's fields to ``statement`` and return the intent with its digest.

    Parameters
    ----------
    statement : IntentStatement
        A validated statement.
    task_id, operation_id, effect : str
        The declaration's task, operation and effect.
    target : Mapping[str, str]
        The declaration's validated effect identifiers.
    actor : str
        The authenticated sender of the declaration.

    Returns
    -------
    AuthorisationIntent
        The composed document, its canonical bytes and their SHA-256.

    Raises
    ------
    AuthorisationIntentError
        If the canonical intent is larger than :data:`MAX_INTENT_BYTES` or cannot
        be represented canonically.
    """
    document: dict[str, Any] = {
        **statement.document,
        "task_id": task_id,
        "operation_id": operation_id,
        "effect": effect,
        "effect_target": dict(target),
        "actor": actor,
    }
    try:
        canonical = canonical_json(document)
    except AefCanonicalizationError:
        raise _refuse("the intent cannot be written canonically") from None
    if len(canonical) > MAX_INTENT_BYTES:
        raise _refuse(f"the canonical intent exceeds {MAX_INTENT_BYTES} bytes")
    return AuthorisationIntent(document, canonical, hashlib.sha256(canonical).hexdigest())
