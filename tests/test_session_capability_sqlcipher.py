# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — session metrics + capability observations open SQLCipher stores

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.capability_observations import read_observed_capability_index
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import SqlCipherKeyError, sqlcipher_available
from synapse_channel.dashboard_store_feeds import build_sessions_feed, event_store_key
from synapse_channel.participants.session_metric_note import (
    SESSION_METRIC_NOTE_KIND,
    format_session_metric_note,
)
from synapse_channel.participants.session_telemetry import SessionMetrics

pytestmark = pytest.mark.skipif(
    not sqlcipher_available(),
    reason="sqlcipher3-binary not installed",
)


def _seed_encrypted_session_store(tmp_path: Path) -> tuple[Path, Path]:
    key = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    store = EventStore(db, key_file=key)
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "kind": SESSION_METRIC_NOTE_KIND,
            "text": format_session_metric_note(
                SessionMetrics(
                    turns=2,
                    errors=0,
                    abstentions=0,
                    input_tokens=50,
                    output_tokens=10,
                    cost_usd=0.01,
                    total_latency_seconds=0.5,
                    max_rate_limit_utilisation=None,
                    last_input_tokens=50,
                )
            ),
            "author": "participant/probe",
            "task_id": "session-enc",
        },
        ts=1.0,
        durable=True,
    )
    store.close()
    return db, key


def _seed_encrypted_capability_store(tmp_path: Path) -> tuple[Path, Path]:
    """Seed release-receipt notes that capability observation folds into evidence."""
    key = generate_key_file(tmp_path / "cap.key")
    db = tmp_path / "cap.db"
    store = EventStore(db, key_file=key)
    store.append(
        EventKind.LEDGER_TASK,
        {
            "task_id": "ROUTED",
            "title": "Python routing cleanup",
            "description": "Improve deterministic route fallback.",
            "depends_on": [],
            "status": "done",
            "suggested_owner": "",
            "created_by": "planner",
            "created_at": 1.0,
            "updated_at": 2.0,
        },
        ts=1.0,
        durable=True,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "ROUTED",
            "author": "FAST",
            "kind": "assessment",
            "text": (
                "release receipt: evidence=pytest tests/test_routing.py -q; "
                "changed_files=src/synapse_channel/core/semantic_routing.py; "
                "epistemic_status=supported"
            ),
            "posted_at": 3.0,
        },
        ts=3.0,
        durable=True,
    )
    store.close()
    return db, key


def test_participant_costs_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key = _seed_encrypted_session_store(tmp_path)
    code = cli.main(
        [
            "participant",
            "costs",
            str(db),
            "--db-key-file",
            str(key),
            "--json",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "session-enc" in out or "participant/probe" in out


def test_participant_costs_without_key_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, _key = _seed_encrypted_session_store(tmp_path)
    code = cli.main(["participant", "costs", str(db), "--json"])
    assert code != 0
    err = capsys.readouterr().err.lower()
    assert "session-enc" not in err
    assert any(
        token in err
        for token in ("key", "sqlcipher", "encrypt", "cipher", "db-key-file", "database")
    )


def test_build_sessions_feed_honours_sqlcipher_key_context(tmp_path: Path) -> None:
    db, key = _seed_encrypted_session_store(tmp_path)
    with event_store_key(key):
        document = build_sessions_feed(db)
    assert isinstance(document, dict)
    sessions = document.get("sessions") or []
    assert sessions or document.get("totals") is not None


def test_build_sessions_feed_without_key_fails_closed(tmp_path: Path) -> None:
    db, _key = _seed_encrypted_session_store(tmp_path)
    with pytest.raises((SqlCipherKeyError, ValueError, OSError)):
        build_sessions_feed(db)


def test_read_observed_capability_index_reads_encrypted_store(tmp_path: Path) -> None:
    db, key = _seed_encrypted_capability_store(tmp_path)
    index = read_observed_capability_index(db, key_file=key)
    agents = [item.agent for item in index.evidence]
    assert "FAST" in agents


def test_read_observed_capability_index_without_key_fails_closed(tmp_path: Path) -> None:
    db, _key = _seed_encrypted_capability_store(tmp_path)
    with pytest.raises((SqlCipherKeyError, ValueError, OSError)):
        read_observed_capability_index(db)
