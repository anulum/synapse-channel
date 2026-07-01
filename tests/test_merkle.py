# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — event-log Merkle commitment regressions

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from synapse_channel.core.journal import EventKind
from synapse_channel.core.merkle import (
    EMPTY_ROOT,
    build_proof,
    build_root,
    leaf_hash,
    proof_from_json,
    proof_to_json,
    render_proof_markdown,
    render_root_markdown,
    root_to_json,
    run_proof,
    run_root,
    verify_inclusion,
    verify_root,
)
from synapse_channel.core.persistence import EventStore, StoredEvent


def _event(seq: int, kind: str = EventKind.CLAIM, **payload: object) -> StoredEvent:
    payload.setdefault("task_id", f"T{seq}")
    return StoredEvent(seq=seq, ts=float(seq), kind=kind, payload=payload)


def _events(count: int) -> tuple[StoredEvent, ...]:
    return tuple(_event(i) for i in range(1, count + 1))


def _seed(path: Path, events: tuple[StoredEvent, ...]) -> None:
    store = EventStore(path)
    for event in events:
        store.append(event.kind, event.payload, ts=event.ts)
    store.close()


def _node(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


# --- RFC 6962 known-answer construction -------------------------------------


def test_empty_log_root_is_sha256_of_empty_string() -> None:
    root = build_root(())
    assert root.tree_size == 0
    assert root.root == hashlib.sha256(b"").hexdigest()
    assert root.root == EMPTY_ROOT


def test_single_leaf_root_matches_rfc6962_leaf_hash() -> None:
    events = _events(1)
    root = build_root(events)
    assert root.root == leaf_hash(events[0]).hex()
    assert root.tree_size == 1


def test_two_leaf_root_matches_hand_computed_node() -> None:
    events = _events(2)
    expected = _node(leaf_hash(events[0]), leaf_hash(events[1])).hex()
    assert build_root(events).root == expected


def test_three_leaf_root_splits_at_largest_power_of_two() -> None:
    # RFC 6962: MTH(3) = node( node(l0,l1), l2 ).
    events = _events(3)
    left = _node(leaf_hash(events[0]), leaf_hash(events[1]))
    expected = _node(left, leaf_hash(events[2])).hex()
    assert build_root(events).root == expected


def test_root_is_order_independent_on_input() -> None:
    events = _events(9)
    assert build_root(tuple(reversed(events))).root == build_root(events).root


# --- inclusion proofs --------------------------------------------------------


@pytest.mark.parametrize("count", [1, 2, 3, 4, 7, 8, 16, 31])
def test_every_leaf_proof_verifies_against_the_root(count: int) -> None:
    events = _events(count)
    root = build_root(events)
    for event in events:
        proof = build_proof(events, event.seq)
        assert proof is not None
        assert proof.root == root.root
        assert verify_inclusion(proof) is True


def test_proof_records_position_and_leaf() -> None:
    events = _events(5)
    proof = build_proof(events, 3)
    assert proof is not None
    assert proof.seq == 3
    assert proof.index == 2
    assert proof.tree_size == 5
    assert proof.leaf == leaf_hash(events[2]).hex()


def test_single_event_proof_has_empty_path() -> None:
    proof = build_proof(_events(1), 1)
    assert proof is not None
    assert proof.path == ()
    assert verify_inclusion(proof) is True


def test_proof_for_absent_sequence_is_none() -> None:
    assert build_proof(_events(4), 99) is None


def test_through_seq_limits_the_committed_tree() -> None:
    events = _events(10)
    root = build_root(events, through_seq=5)
    assert root.tree_size == 5
    assert root.last_seq == 5
    assert root.through_seq == 5
    # An event past the ceiling cannot be proven against the truncated tree.
    assert build_proof(events, 8, through_seq=5) is None
    proof = build_proof(events, 4, through_seq=5)
    assert proof is not None
    assert verify_inclusion(proof) is True


# --- verification rejections -------------------------------------------------


def test_verify_rejects_tampered_leaf() -> None:
    proof = build_proof(_events(6), 4)
    assert proof is not None
    assert verify_inclusion(replace(proof, leaf="00" * 32)) is False


def test_verify_rejects_tampered_path() -> None:
    proof = build_proof(_events(6), 4)
    assert proof is not None
    tampered = (*proof.path[:-1], "11" * 32)
    assert verify_inclusion(replace(proof, path=tampered)) is False


def test_verify_rejects_out_of_range_index() -> None:
    proof = build_proof(_events(6), 4)
    assert proof is not None
    assert verify_inclusion(replace(proof, index=-1)) is False
    assert verify_inclusion(replace(proof, index=6)) is False


def test_verify_rejects_wrong_length_path() -> None:
    proof = build_proof(_events(6), 4)
    assert proof is not None
    assert verify_inclusion(replace(proof, path=proof.path[:-1])) is False


def test_verify_rejects_non_hex_leaf() -> None:
    proof = build_proof(_events(6), 4)
    assert proof is not None
    assert verify_inclusion(replace(proof, leaf="not-hex")) is False


def test_verify_rejects_mismatched_root() -> None:
    proof = build_proof(_events(6), 4)
    assert proof is not None
    assert verify_inclusion(replace(proof, root="ab" * 32)) is False


def test_verify_root_is_case_and_whitespace_insensitive() -> None:
    root = build_root(_events(3)).root
    assert verify_root(root, f"  {root.upper()}  ") is True
    assert verify_root(root, "deadbeef") is False


# --- store-backed runners ----------------------------------------------------


def test_run_root_loads_store(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    events = _events(7)
    _seed(db, events)
    assert run_root(db).root == build_root(events).root


def test_run_proof_loads_store(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    events = _events(7)
    _seed(db, events)
    proof = run_proof(db, 3)
    assert proof is not None
    assert verify_inclusion(proof) is True


def test_run_root_missing_store_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing event store"):
        run_root(tmp_path / "absent.db")


def test_run_proof_missing_store_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing event store"):
        run_proof(tmp_path / "absent.db", 1)


# --- JSON + rendering --------------------------------------------------------


def test_proof_json_roundtrip() -> None:
    proof = build_proof(_events(9), 6)
    assert proof is not None
    restored = proof_from_json(proof_to_json(proof))
    assert restored == proof
    assert verify_inclusion(restored) is True


def test_proof_from_json_rejects_missing_field() -> None:
    with pytest.raises(ValueError, match="malformed inclusion proof"):
        proof_from_json({"seq": 1, "index": 0, "tree_size": 1, "leaf": "ab", "root": "cd"})


def test_proof_from_json_rejects_non_list_path() -> None:
    payload = proof_to_json(build_proof(_events(3), 2))  # type: ignore[arg-type]
    payload["path"] = "not-a-list"
    with pytest.raises(ValueError, match="malformed inclusion proof"):
        proof_from_json(payload)


def test_proof_from_json_rejects_non_integer_index() -> None:
    payload = proof_to_json(build_proof(_events(3), 2))  # type: ignore[arg-type]
    payload["index"] = "middle"
    with pytest.raises(ValueError, match="malformed inclusion proof"):
        proof_from_json(payload)


def test_root_json_shape() -> None:
    payload = root_to_json(build_root(_events(4)))
    assert payload["tree_size"] == 4
    assert payload["through_seq"] == 0


def test_render_root_empty_and_populated() -> None:
    assert "empty log" in render_root_markdown(build_root(()))
    whole = render_root_markdown(build_root(_events(4)))
    assert "whole log" in whole
    assert "Sequence range: 1..4" in whole
    windowed = render_root_markdown(build_root(_events(10), through_seq=6))
    assert "through seq 6" in windowed


def test_render_proof_with_and_without_path() -> None:
    multi = render_proof_markdown(build_proof(_events(5), 3))  # type: ignore[arg-type]
    assert "Audit path" in multi
    assert multi.count("  - ") >= 1
    single = render_proof_markdown(build_proof(_events(1), 1))  # type: ignore[arg-type]
    assert "single-event log" in single
