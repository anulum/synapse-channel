# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the criteria of a raise, their count, the judgement and the raise
"""Name what a raise of a scope's level must show, and decode the three requests.

A raise is one level at a time. For it, an identity holding ``operation-level``
declares *criteria*: the scope, the target level (the current one plus one)
and a list of countable items. Each item is one of:

* ``verified_effects:<effect>`` with a ``min``: operations of that declared
  effect in the scope, closed by a ``verified`` verification from an identity
  other than the declarer;
* ``incorrect_acceptances`` with a ``max``: such operations later read
  ``not_verified`` by an identity other than the declarer;
* ``unknown_outcomes`` with a ``max``: executors' reports of an ``unknown``
  outcome, and attempts whose intent's deadline passed with the operation
  unverified;
* ``stops_at_rest`` with a ``min``: stops of a scope that contains this one
  whose state is ``at rest``.

The hub counts the items from the journal each time it is asked, never from a
stored counter, and only after the criteria's own sequence and the scope's
latest step down. A judge who is not an actor of the scope then records
whether the criteria are met, and a person holding ``operation-level`` raises
the level on that judgement while the count, taken again, still meets every
item. What the journal cannot count (an isolated, qualified executor,
conformance cases, an evidence bundle verified offline, independence) stays
with the person who grants the raise.

This module is pure; the hub counts and records.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.aef_canonical import canonical_json
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.operation_levels import LEVELS
from synapse_channel.core.operation_stops import parse_scope

ITEM_VERIFIED_EFFECTS = "verified_effects"
ITEM_INCORRECT_ACCEPTANCES = "incorrect_acceptances"
ITEM_UNKNOWN_OUTCOMES = "unknown_outcomes"
ITEM_STOPS_AT_REST = "stops_at_rest"
MINIMUM_ITEMS = (ITEM_STOPS_AT_REST,)
MAXIMUM_ITEMS = (ITEM_INCORRECT_ACCEPTANCES, ITEM_UNKNOWN_OUTCOMES)
"""The countable items: ``verified_effects:<effect>`` and these, each with its bound."""

MET = "met"
NOT_MET = "not_met"
VERDICTS = (MET, NOT_MET)

INDEPENDENCE_UNASSESSED = "unassessed"
"""The judge's independence class while the hub holds no facts to assess it."""

UNCOUNTED = (
    "an isolated, qualified executor; conformance cases; an evidence bundle verified "
    "offline; the judge's independence: these the granting person weighs, not the hub"
)
"""What a raise record says the hub could not count."""

CRITERIA_RECORDED = "criteria_recorded"
JUDGEMENT_RECORDED = "judgement_recorded"
RAISED = "raised"
NOT_PERMITTED = "not_permitted"
NOT_ONE_LEVEL = "not_one_level"
CRITERIA_UNKNOWN = "criteria_unknown"
JUDGE_IS_ACTOR = "judge_is_actor"
JUDGEMENT_MISSING = "judgement_missing"
CRITERIA_UNMET = "criteria_unmet"
NOT_A_PERSON = "not_a_person"
SCOPE_STOPPED = "scope_stopped"
"""The answers to the three requests; every one is journalled."""

_MAX_ITEMS = 16
_MAX_BOUND = 1_000_000
_MAX_KEY = 256


class LevelCriteriaError(SynapseError, ValueError):
    """Raised when a request about criteria, a judgement or a raise is malformed."""

    code = "level_criteria"


@dataclass(frozen=True)
class CriteriaItem:
    """One countable item and its bound: ``bound`` is a minimum, or a maximum when ``at_most``."""

    item: str
    bound: int
    at_most: bool

    def to_json(self) -> dict[str, Any]:
        """Return the item as a request and a row state it."""
        return {"item": self.item, ("max" if self.at_most else "min"): self.bound}


@dataclass(frozen=True)
class CriteriaRequest:
    """One decoded ``operation_level_criteria`` frame."""

    scope: str
    target: str
    items: tuple[CriteriaItem, ...]
    idem_key: str


@dataclass(frozen=True)
class JudgementRequest:
    """One decoded ``operation_level_judgement`` frame; ``scope`` is the criteria's own."""

    scope: str
    digest: str
    verdict: str
    idem_key: str


@dataclass(frozen=True)
class RaiseRequest:
    """One decoded ``operation_level_raise`` frame."""

    scope: str
    target: str
    digest: str
    judgement_seq: int
    idem_key: str


def _key(data: Mapping[str, Any]) -> str:
    value = data.get("idem_key")
    if not isinstance(value, str) or not 0 < len(value) <= _MAX_KEY:
        raise LevelCriteriaError(f"idem_key must be text of 1 to {_MAX_KEY} characters")
    return value


def _scope_and_target(data: Mapping[str, Any], effects: Collection[str]) -> tuple[str, str]:
    try:
        scope = parse_scope(data.get("scope"), effects)
    except ValueError as exc:
        raise LevelCriteriaError(str(exc)) from None
    target = data.get("target")
    if target not in LEVELS:
        raise LevelCriteriaError(f"target must be one of {list(LEVELS)}")
    return scope, str(target)


def _item(value: object, effects: Collection[str]) -> CriteriaItem:
    if not isinstance(value, Mapping):
        raise LevelCriteriaError("each item is an object with item and min or max")
    name = value.get("item")
    kind, _, effect = str(name).partition(":")
    if kind == ITEM_VERIFIED_EFFECTS and effect in effects:
        bound_name = "min"
    elif name in MINIMUM_ITEMS:
        bound_name = "min"
    elif name in MAXIMUM_ITEMS:
        bound_name = "max"
    else:
        raise LevelCriteriaError("an item is not in the countable vocabulary")
    bound = value.get(bound_name)
    if set(value) != {"item", bound_name} or type(bound) is not int or not 0 <= bound <= _MAX_BOUND:
        raise LevelCriteriaError(f"item {name} names exactly item and a {bound_name} of 0 or more")
    return CriteriaItem(str(name), bound, bound_name == "max")


def decode_criteria_request(data: Mapping[str, Any], effects: Collection[str]) -> CriteriaRequest:
    """Decode one ``operation_level_criteria`` frame; an item outside the vocabulary is refused.

    Raises
    ------
    LevelCriteriaError
        If the scope, the target, an item or the key is malformed, or two
        items count the same thing.
    """
    scope, target = _scope_and_target(data, effects)
    raw = data.get("items")
    if not isinstance(raw, list) or not 0 < len(raw) <= _MAX_ITEMS:
        raise LevelCriteriaError(f"items must list 1 to {_MAX_ITEMS} countable items")
    items = tuple(_item(value, effects) for value in raw)
    if len({item.item for item in items}) != len(items):
        raise LevelCriteriaError("each item is counted once")
    return CriteriaRequest(scope, target, items, _key(data))


def decode_judgement_request(data: Mapping[str, Any], effects: Collection[str]) -> JudgementRequest:
    """Decode one ``operation_level_judgement`` frame."""
    try:
        scope = parse_scope(data.get("scope"), effects)
    except ValueError as exc:
        raise LevelCriteriaError(str(exc)) from None
    digest = data.get("digest")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        raise LevelCriteriaError("digest must be 64 lower-case hexadecimal characters")
    verdict = data.get("verdict")
    if verdict not in VERDICTS:
        raise LevelCriteriaError(f"verdict must be one of {list(VERDICTS)}")
    return JudgementRequest(scope, digest, str(verdict), _key(data))


def decode_raise_request(data: Mapping[str, Any], effects: Collection[str]) -> RaiseRequest:
    """Decode one ``operation_level_raise`` frame."""
    scope, target = _scope_and_target(data, effects)
    judged = decode_judgement_request({**data, "verdict": MET}, effects)
    seq = data.get("judgement_seq")
    if type(seq) is not int or not 0 < seq < 2**53:
        raise LevelCriteriaError("judgement_seq must be a journal sequence")
    return RaiseRequest(scope, target, judged.digest, seq, judged.idem_key)


def criteria_digest(scope: str, target: str, items: tuple[CriteriaItem, ...]) -> str:
    """Return the SHA-256 of the criteria's canonical JSON."""
    document = {"scope": scope, "target": target, "items": [item.to_json() for item in items]}
    return hashlib.sha256(canonical_json(document)).hexdigest()


def unmet(items: tuple[CriteriaItem, ...], counts: Mapping[str, int]) -> tuple[str, ...]:
    """Return each item the counts do not meet, with its count and bound."""
    found: list[str] = []
    for item in items:
        count = counts.get(item.item, 0)
        if (item.at_most and count > item.bound) or (not item.at_most and count < item.bound):
            relation = "at most" if item.at_most else "at least"
            found.append(f"{item.item}: {count}, needs {relation} {item.bound}")
    return tuple(found)
