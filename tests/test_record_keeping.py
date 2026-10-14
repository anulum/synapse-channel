# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — public retained bundle tests
"""Verify bundles against owning Merkle primitives and real journal writes."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.event_row_mac import load_or_create_row_mac_key, row_mac_key_path
from synapse_channel.core.merkle import EMPTY_ROOT, build_root
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.record_keeping import (
    build_export,
    export_to_json,
    render_human,
    utc_event_time,
)


def test_complete_bundle_matches_public_merkle_and_chronological_extrema(tmp_path: Path) -> None:
    path = tmp_path / "bundle.db"
    store = EventStore(path)
    try:
        store.append("claim", {"task_id": "T", "secret": "never-in-metadata"}, ts=30)
        store.append("finding", {"task_id": "T"}, ts=-1)
        store.append("chat", {"text": "private"}, ts=10)
        expected = build_root(tuple(store.iter_events()))
        export = build_export(path)
        assert export.merkle == expected
        assert (export.first_ts, export.last_ts) == (-1, 30)
        assert [entry.purpose for entry in export.entries] == [
            "operation_record",
            "risk_indicator",
            "general_record",
        ]
        data = export_to_json(export)
        assert data["entry_count"] == 3
        assert data["purpose_counts"] == {
            "operation_record": 1,
            "risk_indicator": 1,
            "general_record": 1,
        }
        assert "never-in-metadata" not in str(data)
        assert "1969-12-31T23:59:59+00:00" in render_human(export)
        prefix = build_export(path, through_seq=2)
        assert prefix.merkle == build_root(tuple(store.iter_events()), through_seq=2)
        assert len(prefix.entries) == 2
        empty = build_export(path, through_seq=0)
        assert empty.merkle.root == EMPTY_ROOT
        assert "Time span: empty" in render_human(empty)
    finally:
        store.close()


def test_bundle_uses_existing_row_key_without_provisioning_replacement(tmp_path: Path) -> None:
    path = tmp_path / "signed.db"
    store = EventStore(path)
    key_path = row_mac_key_path(path)
    try:
        key = load_or_create_row_mac_key(key_path, current_max_seq=0, log_has_macs=False)
        store.enable_row_mac(key)
        store.append("checkpoint", {"task_id": "T"}, ts=1)
        original_key = key_path.read_bytes()
        export = build_export(path, row_mac_key_file=key_path)
        assert export.entries[0].row_mac_verified
        assert export_to_json(export)["row_authentication"] == {
            "verified_events": 1,
            "unverified_events": 0,
            "scope": "Row MAC verification is distinct from a Merkle commitment or signed witness.",
        }
        assert key_path.read_bytes() == original_key
        absent = tmp_path / "absent.key"
        with pytest.raises(ValueError):
            build_export(path, row_mac_key_file=absent)
        assert not absent.exists()
    finally:
        store.close()


@pytest.mark.parametrize("timestamp", [True, float("nan"), float("inf"), -float("inf"), 1e300])
def test_public_utc_clock_refuses_unrepresentable_values(timestamp: float) -> None:
    with pytest.raises(ValueError):
        utc_event_time(timestamp)
