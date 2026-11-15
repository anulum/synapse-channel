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


def _hit(
    *,
    source: str = "trace.md",
    kind: str = "trace",
    score: float | None = 0.75,
    snippet: str = "A bounded memory",
    presentation: str = "validated",
    provenance: str = "REMANENTIA /recall",
) -> MemoryHit:
    return MemoryHit(
        source=source,
        kind=kind,
        score=score,
        snippet=snippet,
        presentation=presentation,
        provenance=provenance,
    )


def _malformed_hit(**changes: object) -> MemoryHit:
    fields: dict[str, object] = {
        "source": "trace.md",
        "kind": "trace",
        "score": 0.75,
        "snippet": "A bounded memory",
        "presentation": "validated",
        "provenance": "REMANENTIA /recall",
    }
    fields.update(changes)
    return MemoryHit(**fields)  # type: ignore[arg-type] # wrong shape on purpose


@pytest.mark.parametrize("mode", ["validated", "boundary", "refuted"])
def test_known_presentations_are_preserved(mode: str) -> None:
    assert normalise_presentation(mode) == mode


@pytest.mark.parametrize("mode", ["trusted", "", None, 1])
def test_unknown_presentations_floor_to_boundary(mode: object) -> None:
    assert normalise_presentation(mode) == "boundary"
    if mode == "trusted":
        hit = _hit(presentation="trusted")
    elif mode == "":
        hit = _hit(presentation="")
    elif mode is None:
        hit = _malformed_hit(presentation=None)
    else:
        hit = _malformed_hit(presentation=1)
    assert hit.presentation == "boundary"


def test_hit_is_frozen_and_discards_non_finite_scores() -> None:
    assert _hit(score=float("nan")).score is None
    assert _hit(score=float("inf")).score is None
    assert _hit(score=2).score == 2.0
    hit = _hit()
    with pytest.raises(FrozenInstanceError):
        hit.source = "changed"  # type: ignore[misc] # frozen probe


@pytest.mark.parametrize("field", ["source", "kind", "snippet", "provenance"])
def test_hit_requires_non_empty_text_fields(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        if field == "source":
            _hit(source="  ")
        elif field == "kind":
            _hit(kind="  ")
        elif field == "snippet":
            _hit(snippet="  ")
        else:
            _hit(provenance="  ")


@pytest.mark.parametrize("score", [True, "0.8", object()])
def test_hit_rejects_non_numeric_scores(score: object) -> None:
    with pytest.raises(ValueError, match="score"):
        _malformed_hit(score=score)


def test_recall_result_pins_abstention_and_tuple_shape() -> None:
    hit = _hit()
    result = MemoryRecallResult("q", (hit,), False, "REMANENTIA", "boundary data")
    assert result.hits == (hit,)
    assert MemoryRecallResult("q", (), True, "REMANENTIA", "no hits").abstained
    with pytest.raises(FrozenInstanceError):
        result.note = "changed"  # type: ignore[misc] # frozen probe
    with pytest.raises(ValueError, match="exactly when"):
        MemoryRecallResult("q", (), False, "REMANENTIA", "bad")
    with pytest.raises(ValueError, match="exactly when"):
        MemoryRecallResult("q", (hit,), True, "REMANENTIA", "bad")


def _malformed_recall(**changes: object) -> MemoryRecallResult:
    fields: dict[str, object] = {
        "query": "q",
        "hits": (),
        "abstained": True,
        "source": "REMANENTIA",
        "note": "none",
    }
    fields.update(changes)
    return MemoryRecallResult(**fields)  # type: ignore[arg-type] # wrong shape on purpose


def _malformed_policy(**changes: object) -> MemoryPolicy:
    values: dict[str, object] = {
        "timeout_seconds": 2.0,
        "top_k": 3,
        "max_chars": 4096,
    }
    values.update(changes)
    return MemoryPolicy(**values)  # type: ignore[arg-type] # wrong numeric shape on purpose


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
    with pytest.raises(ValueError, match=message):
        if "query" in changes:
            _malformed_recall(query=1)
        elif "source" in changes:
            MemoryRecallResult("q", (), True, "", "none")
        elif "note" in changes:
            _malformed_recall(note=1)
        elif isinstance(changes.get("hits"), list):
            _malformed_recall(hits=[_hit()])
        elif "hits" in changes:
            _malformed_recall(hits=(object(),))
        else:
            _malformed_recall(abstained=1)


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
        if changes.get("timeout_seconds") is True:
            _malformed_policy(timeout_seconds=True)
        elif changes.get("timeout_seconds") == 0:
            MemoryPolicy(timeout_seconds=0)
        elif changes.get("timeout_seconds") == 31:
            MemoryPolicy(timeout_seconds=31)
        elif isinstance(changes.get("timeout_seconds"), float):
            MemoryPolicy(timeout_seconds=float("nan"))
        elif changes.get("top_k") is True:
            _malformed_policy(top_k=True)
        elif changes.get("top_k") == 1.5:
            _malformed_policy(top_k=1.5)
        elif changes.get("top_k") == 0:
            MemoryPolicy(top_k=0)
        elif changes.get("top_k") == 21:
            MemoryPolicy(top_k=21)
        elif changes.get("max_chars") is True:
            _malformed_policy(max_chars=True)
        elif changes.get("max_chars") == 512.5:
            _malformed_policy(max_chars=512.5)
        elif changes.get("max_chars") == 511:
            MemoryPolicy(max_chars=511)
        else:
            MemoryPolicy(max_chars=16385)


def test_memory_policy_normalises_timeout_and_is_frozen() -> None:
    policy = MemoryPolicy(timeout_seconds=2, top_k=5, max_chars=512)
    assert policy.timeout_seconds == 2.0
    with pytest.raises(FrozenInstanceError):
        policy.top_k = 4  # type: ignore[misc] # frozen probe
