# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — the in-memory and crash-safe persistent AES-GCM message counters

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.at_rest_counter import (
    InMemoryMessageCounter,
    MessageCounter,
    PersistentMessageCounter,
)

# --- in-memory ---------------------------------------------------------------------------


def test_in_memory_counter_starts_at_zero_and_increments() -> None:
    counter = InMemoryMessageCounter()
    assert counter.count == 0
    assert counter.increment() == 1
    assert counter.increment() == 2
    assert counter.count == 2


def test_in_memory_counter_can_start_at_a_seed() -> None:
    counter = InMemoryMessageCounter(41)
    assert counter.count == 41
    assert counter.increment() == 42


def test_in_memory_counter_rejects_a_negative_seed() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        InMemoryMessageCounter(-1)


def test_both_counters_satisfy_the_protocol(tmp_path: Path) -> None:
    assert isinstance(InMemoryMessageCounter(), MessageCounter)
    assert isinstance(PersistentMessageCounter(tmp_path / "c"), MessageCounter)


# --- persistent: durability and reserve-ahead --------------------------------------------


def test_persistent_counter_is_empty_until_first_use(tmp_path: Path) -> None:
    path = tmp_path / "count"
    counter = PersistentMessageCounter(path)
    assert counter.count == 0
    assert not path.exists()  # no write until a batch boundary is crossed


def test_persistent_counter_reserves_a_batch_ahead_on_the_boundary(tmp_path: Path) -> None:
    path = tmp_path / "count"
    counter = PersistentMessageCounter(path, batch_size=4)
    assert counter.increment() == 1  # crosses into the first batch, reserves 4
    assert path.read_text(encoding="utf-8") == "4"
    for expected in (2, 3, 4):
        assert counter.increment() == expected
    assert path.read_text(encoding="utf-8") == "4"  # still within the reservation, no rewrite
    assert counter.increment() == 5  # crosses into the next batch, reserves 8
    assert path.read_text(encoding="utf-8") == "8"


def test_persistent_counter_resumes_from_the_reservation_after_a_crash(tmp_path: Path) -> None:
    # Three increments reserve one batch of four and never close; a fresh counter (a restart
    # after a crash) resumes from the reserved four — at or above the true three, never below,
    # so the key over-counts by less than a batch and rekeys early rather than reusing a nonce.
    path = tmp_path / "count"
    crashed = PersistentMessageCounter(path, batch_size=4)
    for _ in range(3):
        crashed.increment()
    assert crashed.count == 3

    resumed = PersistentMessageCounter(path, batch_size=4)
    assert resumed.count == 4  # conservative: resumes from the persisted reservation
    assert resumed.increment() == 5


def test_persistent_counter_close_records_the_exact_count_for_a_clean_resume(
    tmp_path: Path,
) -> None:
    path = tmp_path / "count"
    counter = PersistentMessageCounter(path, batch_size=1024)
    for _ in range(5):
        counter.increment()
    counter.close()
    assert path.read_text(encoding="utf-8") == "5"

    resumed = PersistentMessageCounter(path, batch_size=1024)
    assert resumed.count == 5  # exact, not the reserved 1024
    assert resumed.increment() == 6


# --- persistent: fail-closed on a bad sidecar --------------------------------------------


def test_persistent_counter_refuses_a_non_integer_file(tmp_path: Path) -> None:
    path = tmp_path / "count"
    path.write_text("not-a-number", encoding="utf-8")
    with pytest.raises(ValueError, match="is not an integer"):
        PersistentMessageCounter(path)


def test_persistent_counter_refuses_a_negative_file(tmp_path: Path) -> None:
    path = tmp_path / "count"
    path.write_text("-5", encoding="utf-8")
    with pytest.raises(ValueError, match="negative count"):
        PersistentMessageCounter(path)


def test_persistent_counter_rejects_a_zero_batch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        PersistentMessageCounter(tmp_path / "count", batch_size=0)
