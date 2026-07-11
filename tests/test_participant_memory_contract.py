# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Participant memory contract
"""Pin structural honesty and immutable bounds of recalled memory records."""

from dataclasses import FrozenInstanceError

import pytest

from synapse_channel.participants.memory_contract import (
    MemoryHit,
    MemoryPolicy,
    MemoryRecallResult,
    normalise_presentation,
)


def _hit(**changes: object) -> MemoryHit:
    fields: dict[str, object] = {
        "source": "trace.md",
        "kind": "trace",
        "score": 0.75,
        "snippet": "A bounded memory",
        "presentation": "validated",
        "provenance": "REMANENTIA /recall",
    }
    fields.update(changes)
    return MemoryHit(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize("mode", ["validated", "boundary", "refuted"])
def test_known_presentations_are_preserved(mode: str) -> None:
    assert normalise_presentation(mode) == mode


@pytest.mark.parametrize("mode", ["trusted", "", None, 1])
def test_unknown_presentations_floor_to_boundary(mode: object) -> None:
    assert normalise_presentation(mode) == "boundary"
    assert _hit(presentation=mode).presentation == "boundary"


def test_hit_is_frozen_and_discards_non_finite_scores() -> None:
    assert _hit(score=float("nan")).score is None
    assert _hit(score=float("inf")).score is None
    assert _hit(score=2).score == 2.0
    hit = _hit()
    with pytest.raises(FrozenInstanceError):
        hit.source = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("field", ["source", "kind", "snippet", "provenance"])
def test_hit_requires_non_empty_text_fields(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        _hit(**{field: "  "})


@pytest.mark.parametrize("score", [True, "0.8", object()])
def test_hit_rejects_non_numeric_scores(score: object) -> None:
    with pytest.raises(ValueError, match="score"):
        _hit(score=score)


def test_recall_result_pins_abstention_and_tuple_shape() -> None:
    hit = _hit()
    result = MemoryRecallResult("q", (hit,), False, "REMANENTIA", "boundary data")
    assert result.hits == (hit,)
    assert MemoryRecallResult("q", (), True, "REMANENTIA", "no hits").abstained
    with pytest.raises(FrozenInstanceError):
        result.note = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="exactly when"):
        MemoryRecallResult("q", (), False, "REMANENTIA", "bad")
    with pytest.raises(ValueError, match="exactly when"):
        MemoryRecallResult("q", (hit,), True, "REMANENTIA", "bad")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"query": 1}, "query"),
        ({"source": ""}, "source"),
        ({"note": 1}, "note"),
        ({"hits": [_hit()]}, "tuple"),
        ({"hits": (object(),)}, "tuple"),
        ({"abstained": 1}, "boolean"),
    ],
)
def test_recall_result_rejects_malformed_fields(changes: dict[str, object], message: str) -> None:
    fields: dict[str, object] = {
        "query": "q",
        "hits": (),
        "abstained": True,
        "source": "REMANENTIA",
        "note": "none",
    }
    fields.update(changes)
    with pytest.raises(ValueError, match=message):
        MemoryRecallResult(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"timeout_seconds": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": 31},
        {"timeout_seconds": float("nan")},
        {"top_k": True},
        {"top_k": 1.5},
        {"top_k": 0},
        {"top_k": 21},
        {"max_chars": True},
        {"max_chars": 512.5},
        {"max_chars": 511},
        {"max_chars": 16385},
    ],
)
def test_memory_policy_rejects_unbounded_values(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        MemoryPolicy(**changes)  # type: ignore[arg-type]


def test_memory_policy_normalises_timeout_and_is_frozen() -> None:
    policy = MemoryPolicy(timeout_seconds=2, top_k=5, max_chars=512)
    assert policy.timeout_seconds == 2.0
    with pytest.raises(FrozenInstanceError):
        policy.top_k = 4  # type: ignore[misc]
