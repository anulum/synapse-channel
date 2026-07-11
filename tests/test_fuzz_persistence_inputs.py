# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — property-based fuzzing for event-store persistence
"""Property-based fuzz targets for the production SQLite event store."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from hypothesis import example, given, settings
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from synapse_channel.core.persistence import EventStore, StoredEvent

_FUZZ_EXAMPLES = int(os.environ.get("SYNAPSE_FUZZ_EXAMPLES", "100"))
if not 1 <= _FUZZ_EXAMPLES <= 10_000:
    raise RuntimeError("SYNAPSE_FUZZ_EXAMPLES must be between 1 and 10000")

_SCALARS: SearchStrategy[object] = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=128),
)
_JSON_VALUES: SearchStrategy[object] = st.recursive(
    _SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=8),
        st.dictionaries(st.text(max_size=32), children, max_size=8),
    ),
    max_leaves=32,
)
_PAYLOADS: SearchStrategy[dict[str, object]] = st.dictionaries(
    st.text(max_size=32),
    _JSON_VALUES,
    max_size=8,
)
_EVENTS: SearchStrategy[list[tuple[str, dict[str, object], float, bool]]] = st.lists(
    st.tuples(
        st.sampled_from(("chat", "claim", "finding", "recall", "delivery_receipt")),
        _PAYLOADS,
        st.integers(min_value=-1_000_000_000, max_value=1_000_000_000).map(float),
        st.booleans(),
    ),
    max_size=24,
)


def _remove_database_surfaces(path: Path) -> None:
    """Remove a prior Hypothesis example's SQLite file and WAL sidecars."""
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        candidate.unlink(missing_ok=True)


@given(events=_EVENTS)
@example(events=[("chat", {"payload": "recorded-frame"}, 1.0, False)])
@settings(max_examples=_FUZZ_EXAMPLES, deadline=None, print_blob=True)
def test_event_store_round_trips_generated_events_across_reopen(
    events: list[tuple[str, dict[str, object], float, bool]],
) -> None:
    """Generated JSON-safe events keep order and content across a real reopen."""
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "fuzz-events.db"
        _remove_database_surfaces(database)

        with EventStore(database) as store:
            sequences = [
                store.append(kind, payload, ts=timestamp, durable=durable)
                for kind, payload, timestamp, durable in events
            ]

        with EventStore(database) as reopened:
            persisted = reopened.read_all()

    assert [event.seq for event in persisted] == sequences
    assert [(event.kind, event.payload, event.ts) for event in persisted] == [
        (kind, payload, timestamp) for kind, payload, timestamp, _durable in events
    ]


@given(payloads=st.lists(_PAYLOADS, max_size=32), batch_size=st.integers(min_value=1, max_value=8))
@example(payloads=[{"payload": "first"}, {"payload": "second"}], batch_size=1)
@settings(max_examples=_FUZZ_EXAMPLES, deadline=None, print_blob=True)
def test_event_store_cursor_walk_is_complete_and_duplicate_free(
    payloads: list[dict[str, object]],
    batch_size: int,
) -> None:
    """Bounded cursor reads reproduce the event log once, in sequence order."""
    with EventStore(":memory:") as store:
        for index, payload in enumerate(payloads):
            store.append(f"event-{index % 3}", payload, ts=float(index))
        expected = store.read_all()
        observed: list[StoredEvent] = []
        cursor = 0
        while batch := store.read_since(cursor, limit=batch_size):
            observed.extend(batch)
            cursor = batch[-1].seq
        assert store.read_since(cursor, limit=batch_size) == []

    assert observed == expected


@given(
    payloads=st.lists(_PAYLOADS, max_size=30),
    selected_indices=st.sets(st.integers(min_value=0, max_value=29)),
)
@example(payloads=[{"id": 0}, {"id": 1}, {"id": 2}], selected_indices={0, 2})
@settings(max_examples=_FUZZ_EXAMPLES, deadline=None, print_blob=True)
def test_event_store_delete_removes_only_selected_sequences(
    payloads: list[dict[str, object]],
    selected_indices: set[int],
) -> None:
    """Generated deletion sets remove exactly their existing event sequences."""
    with EventStore(":memory:") as store:
        for index, payload in enumerate(payloads):
            store.append("chat", payload, ts=float(index))
        before = store.read_all()
        selected = {index for index in selected_indices if index < len(before)}
        removed = store.delete(before[index].seq for index in selected)
        remaining = store.read_all()

    assert removed == len(selected)
    assert remaining == [event for index, event in enumerate(before) if index not in selected]
