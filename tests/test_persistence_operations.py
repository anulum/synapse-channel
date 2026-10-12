# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real retained-operation transaction boundaries
"""Exercise non-evicting capacity through the public storage transaction API."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.atomic_operations import OperationRecord
from synapse_channel.core.persistence import EventStore


@pytest.mark.parametrize("capacity", [True, 0, -1, 1.5, 2**53])
def test_invalid_retained_capacity_cannot_insert_partial_history(
    tmp_path: Path, capacity: int
) -> None:
    """Reject a bad cap before touching events, operations or evidence intents."""
    with EventStore(tmp_path / "capacity.db") as store:
        with pytest.raises(ValueError, match="invalid retained operation budget"):
            store.commit_operation(
                operation_key="P/seat/one",
                request_digest="a" * 64,
                response={"accepted": True},
                events=[("chat", {"body": "one"})],
                intent={},
                max_retained_operations=capacity,
            )
        assert store.read_all() == []
        assert store.read_operations() == ()
        assert store.pending_operation_intents() == ()


def test_full_capacity_preserves_historical_replay_conflict_and_atomic_rejection(
    tmp_path: Path,
) -> None:
    """A full journal keeps exact historical results and never evicts to admit work."""
    path = tmp_path / "operations.db"
    with EventStore(path) as store:
        initial = store.commit_operation(
            operation_key="P/seat/one",
            request_digest="a" * 64,
            response={"accepted": True},
            events=[("chat", {"body": "one"})],
            intent={"intent": "one"},
            max_retained_operations=1,
        )
        assert initial.outcome == "inserted"
        before = store.read_all()
        for digest, outcome in [("a" * 64, "replayed"), ("b" * 64, "conflict")]:
            result = store.commit_operation(
                operation_key="P/seat/one",
                request_digest=digest,
                response={"accepted": False},
                events=[("chat", {"body": "must not insert"})],
                intent={},
                required_predecessor=OperationRecord(
                    key="absent", request_digest="c" * 64, response={}
                ),
                max_retained_operations=1,
            )
            assert result.outcome == outcome
            assert result.operation == initial.operation
            assert store.read_all() == before
        with pytest.raises(ValueError, match="retained operation budget exhausted"):
            store.commit_operation(
                operation_key="P/seat/two",
                request_digest="d" * 64,
                response={},
                events=[("chat", {"body": "must not insert"})],
                intent={},
                max_retained_operations=1,
            )
        assert store.read_all() == before
        assert [item.operation_key for item in store.pending_operation_intents()] == ["P/seat/one"]
    with EventStore(path) as reopened:
        assert reopened.get_operation("P/seat/one") == initial.operation
        assert reopened.get_operation("P/seat/two") is None
