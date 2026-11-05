# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — connect-token audit rows
"""Exercise the journal rows a token-set hub writes, against a real event store."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, replay
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.record_keeping import (
    OPERATION_RECORD,
    RISK_INDICATOR,
    purpose_for_kind,
)
from synapse_channel.core.token_audit import (
    AGENT_FIELD_LIMIT,
    REFUSAL_WINDOW_SECONDS,
    TokenAudit,
    token_audit_for,
)
from synapse_channel.core.token_set import TokenSetAuthenticator, connect_token_digest

_START = 1_800_000_000.0
_KINDS = (
    EventKind.CONNECT_TOKEN_ADMITTED,
    EventKind.CONNECT_TOKEN_REFUSED,
    EventKind.CONNECT_TOKEN_SET_APPLIED,
    EventKind.CONNECT_TOKEN_SET_REJECTED,
)


class _Clock:
    def __init__(self) -> None:
        self.now = _START

    def __call__(self) -> float:
        return self.now


def _write(path: Path, *entries: tuple[str, str, dict[str, Any]], raw: str | None = None) -> Path:
    """Replace the set atomically with owner-only mode, as an operator would."""
    tokens = [
        {"id": token_id, "sha256": connect_token_digest(secret), **fields}
        for token_id, secret, fields in entries
    ]
    staging = path.with_name(path.name + ".new")
    staging.write_text(raw if raw is not None else json.dumps({"version": 1, "tokens": tokens}))
    os.chmod(staging, 0o600)
    os.replace(staging, path)
    return path


def _rows(store: EventStore, kind: str) -> list[dict[str, Any]]:
    return [event.payload for event in store.read_all() if event.kind == kind]


def _audit_rows(store: EventStore) -> list[StoredEvent]:
    return [event for event in store.read_all() if event.kind in _KINDS]


@pytest.fixture
def store(tmp_path: Path) -> Any:
    journal = EventStore(tmp_path / "hub.db")
    yield journal
    journal.close()


def test_a_flood_of_refusals_writes_two_rows_with_the_full_count(store: EventStore) -> None:
    clock = _Clock()
    audit = TokenAudit(store, clock=clock)
    for attempt in range(1000):
        clock.now = _START + attempt * 0.05
        audit.refused(None, "unknown", "X" * 500)
    assert len(_rows(store, EventKind.CONNECT_TOKEN_REFUSED)) == 1
    audit.flush()
    rows = _rows(store, EventKind.CONNECT_TOKEN_REFUSED)
    assert [row["count"] for row in rows] == [1, 999]
    assert all(row["reason"] == "unknown" and row["token_id"] is None for row in rows)
    assert all(len(row["agent"]) == AGENT_FIELD_LIMIT for row in rows)
    assert rows[1]["window_start"] == _START
    assert rows[1]["window_end"] == _START + 999 * 0.05
    audit.flush()
    assert len(_rows(store, EventKind.CONNECT_TOKEN_REFUSED)) == 2


def test_windows_are_kept_per_reason_and_token_and_close_after_a_minute(
    store: EventStore,
) -> None:
    clock = _Clock()
    audit = TokenAudit(store, clock=clock)
    audit.refused("ops", "expired", "A/one")
    audit.refused("ops", "expired", "A/two")
    audit.refused("ops", "agent_not_bound", "A/three")
    audit.refused(None, "unknown", "A/four")
    clock.now = _START + REFUSAL_WINDOW_SECONDS + 1
    audit.admitted("ops", "A/five")
    rows = _rows(store, EventKind.CONNECT_TOKEN_REFUSED)
    assert [(row["reason"], row["token_id"], row["count"]) for row in rows] == [
        ("expired", "ops", 1),
        ("agent_not_bound", "ops", 1),
        ("unknown", None, 1),
        ("expired", "ops", 1),
    ]
    assert rows[-1]["agent"] == "A/one"
    assert rows[-1]["window_end"] == _START + REFUSAL_WINDOW_SECONDS
    audit.refused("ops", "expired", "A/six")
    assert _rows(store, EventKind.CONNECT_TOKEN_REFUSED)[-1]["window_start"] == clock.now
    assert _rows(store, EventKind.CONNECT_TOKEN_ADMITTED) == [
        {"token_id": "ops", "agent": "A/five"}
    ]


def test_the_authenticator_reports_each_set_change(tmp_path: Path, store: EventStore) -> None:
    path = _write(tmp_path / "tokens.json", ("a", "s-a", {}))
    authenticator = TokenSetAuthenticator(path)
    authenticator.observe(TokenAudit(store))
    _write(path, raw="{broken")
    assert authenticator.authenticate("s-a", "A") == (True, "Authenticated.")
    _write(path, ("a", "s-a", {}), ("b", "s-b", {}))
    assert authenticator.authenticate("s-b", "A") == (True, "Authenticated.")
    events = _audit_rows(store)
    assert [event.kind for event in events] == [
        EventKind.CONNECT_TOKEN_SET_APPLIED,
        EventKind.CONNECT_TOKEN_SET_REJECTED,
        EventKind.CONNECT_TOKEN_SET_APPLIED,
    ]
    assert events[0].payload == {"path": str(path), "token_ids": ["a"]}
    assert "not valid JSON" in events[1].payload["reason"]
    assert events[2].payload["token_ids"] == ["a", "b"]


def test_observing_a_stale_set_reports_it_at_once(tmp_path: Path, store: EventStore) -> None:
    path = _write(tmp_path / "tokens.json", ("a", "s-a", {}))
    authenticator = TokenSetAuthenticator(path)
    path.unlink()
    authenticator.refresh()
    authenticator.observe(TokenAudit(store))
    kinds = [event.kind for event in _audit_rows(store)]
    assert kinds == [EventKind.CONNECT_TOKEN_SET_APPLIED, EventKind.CONNECT_TOKEN_SET_REJECTED]
    assert "is missing" in _rows(store, EventKind.CONNECT_TOKEN_SET_REJECTED)[0]["reason"]


def test_outcomes_name_the_entry_and_a_stable_code(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "tokens.json",
        ("open", "s-open", {}),
        ("later", "s-later", {"not_before": "2027-02-01T00:00:00Z"}),
        ("over", "s-over", {"not_after": "2027-01-01T00:00:00Z"}),
        ("seat", "s-seat", {"agents": ["A/seat"]}),
    )
    authenticator = TokenSetAuthenticator(path, clock=lambda: _START)
    cases = {
        ("", "A"): (None, "missing"),
        ("nope", "A"): (None, "unknown"),
        ("s-later", "A"): ("later", "not_yet_valid"),
        ("s-over", "A"): ("over", "expired"),
        ("s-seat", "A/other"): ("seat", "agent_not_bound"),
        ("s-seat", "A/seat"): ("seat", "admitted"),
        ("s-open", "A"): ("open", "admitted"),
    }
    for (secret, agent), (token_id, code) in cases.items():
        outcome = authenticator.authenticate_outcome(secret, agent)
        assert (outcome.token_id, outcome.code) == (token_id, code)
        assert outcome.ok is (code == "admitted")
        assert (outcome.principal is not None) is outcome.ok


def test_an_audit_is_attached_only_to_a_token_set_with_a_journal(
    tmp_path: Path, store: EventStore
) -> None:
    path = _write(tmp_path / "tokens.json", ("a", "s-a", {}))
    assert token_audit_for(TokenSetAuthenticator(path), None) is None
    assert token_audit_for(TokenAuthenticator(["s"]), store) is None
    assert token_audit_for(None, store) is None
    assert isinstance(token_audit_for(TokenSetAuthenticator(path), store), TokenAudit)
    assert [event.kind for event in _audit_rows(store)] == [EventKind.CONNECT_TOKEN_SET_APPLIED]


def test_audit_kinds_are_classified_and_skipped_by_replay(store: EventStore) -> None:
    audit = TokenAudit(store)
    audit.set_applied(Path("tokens.json"), ("a",))
    audit.set_rejected(Path("tokens.json"), "bad")
    audit.admitted("a", "A")
    audit.refused(None, "unknown", "A")
    replay(store)
    assert purpose_for_kind(EventKind.CONNECT_TOKEN_REFUSED) == RISK_INDICATOR
    assert purpose_for_kind(EventKind.CONNECT_TOKEN_SET_REJECTED) == RISK_INDICATOR
    assert purpose_for_kind(EventKind.CONNECT_TOKEN_ADMITTED) == OPERATION_RECORD
    assert purpose_for_kind(EventKind.CONNECT_TOKEN_SET_APPLIED) == OPERATION_RECORD


async def test_a_real_hub_journals_admissions_and_refusals(tmp_path: Path) -> None:
    path = _write(tmp_path / "tokens.json", ("ops", "s-ops-secret", {}))
    journal = EventStore(tmp_path / "hub.db")
    try:
        hub = SynapseHub(journal=journal, authenticator=TokenSetAuthenticator(path))
        async with running_hub(hub) as (_, uri):
            async with connect(uri) as websocket:
                await send_json(websocket, sender="A", type="heartbeat", token="s-ops-secret")
                await read_until_type(websocket, "welcome")
            for agent in ("B", "C", "D"):
                async with connect(uri) as websocket:
                    await send_json(websocket, sender=agent, type="heartbeat", token="wrong")
                    await read_until_type(websocket, "auth_denied")
        events = _audit_rows(journal)
        text = json.dumps([event.payload for event in events])
    finally:
        journal.close()
    assert [event.kind for event in events] == [
        EventKind.CONNECT_TOKEN_SET_APPLIED,
        EventKind.CONNECT_TOKEN_ADMITTED,
        EventKind.CONNECT_TOKEN_REFUSED,
        EventKind.CONNECT_TOKEN_REFUSED,
    ]
    assert events[1].payload == {"token_id": "ops", "agent": "A"}
    assert [event.payload["count"] for event in events[2:]] == [1, 2]
    assert "s-ops-secret" not in text and "wrong" not in text
    assert connect_token_digest("s-ops-secret") not in text
