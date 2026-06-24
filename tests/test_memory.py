# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the persistent-memory write-side (recall query-stream)

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, record_finding, record_recall, replay
from synapse_channel.core.persistence import EventStore


class _WS:
    """Minimal hub-side socket capturing what the hub sends back."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, raw: str) -> None:
        self.sent.append(raw)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        return None

    def last(self) -> Any:
        return json.loads(self.sent[-1])


def _msg(**fields: Any) -> str:
    return json.dumps(fields)


def _hub(journal: EventStore | None = None) -> SynapseHub:
    return SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=journal)


def _finding_msg(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "sender": "AUTHOR",
        "type": "finding",
        "statement": "studio bundle validates byte-parity across federation",
        "subkind": "codebase-fact",
        "evidence_kind": "measured",
        "claim_status": "reference-validated",
        "evidence_ref": "tests/test_federation.py:42",
        "provenance": {"project": "SCPN-STUDIO", "session": "s9"},
        "validity": {"valid_from": 1.0},
        # Re-checked this session so freshness derives to verified-at-source — the
        # bar a reference-validated claim must clear (INV-1).
        "verified_at_source": {"checked_this_session": True, "source_ref": "federation run"},
    }
    base.update(overrides)
    return json.dumps(base)


def _findings(store: EventStore) -> list[dict[str, Any]]:
    return [e.payload for e in store.read_all() if e.kind == EventKind.FINDING]


async def test_recall_log_journals_with_hub_attested_origin(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = _hub(journal=store)
    ws = _WS()
    await hub.register(ws)
    await hub.handle_message(
        _msg(
            sender="REASON",
            type="recall_log",
            query_text="what blocked CONTROL last session?",
            returned_claim_ids=["c1", "c2"],
            was_used=True,
            abstained=False,
        ),
        ws,
    )
    events = [e for e in store.read_all() if e.kind == EventKind.RECALL]
    store.close()
    assert len(events) == 1
    rec = events[0].payload
    assert rec["query_text"] == "what blocked CONTROL last session?"
    assert rec["returned_claim_ids"] == ["c1", "c2"]
    assert rec["was_used"] is True
    assert rec["abstained"] is False
    # Origin is hub-attested, not taken from the client.
    assert rec["by"] == "REASON"
    assert isinstance(rec["at"], float)
    # The producer gets a private acknowledgement.
    assert ws.last()["type"] == "recall_logged"
    assert ws.last()["target"] == "REASON"


async def test_recall_log_origin_cannot_be_forged(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = _hub(journal=store)
    ws = _WS()
    await hub.register(ws)
    # A sender that tries to self-report a different identity/time is overridden.
    await hub.handle_message(
        _msg(sender="REASON", type="recall_log", query_text="q", by="CEO", at=1.0),
        ws,
    )
    rec = next(e.payload for e in store.read_all() if e.kind == EventKind.RECALL)
    store.close()
    assert rec["by"] == "REASON"
    assert rec["at"] != 1.0


async def test_recall_log_defaults_and_malformed_ids(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = _hub(journal=store)
    ws = _WS()
    await hub.register(ws)
    # returned_claim_ids is not a list; was_used/abstained omitted.
    await hub.handle_message(
        _msg(sender="A", type="recall_log", query_text="q", returned_claim_ids="oops"),
        ws,
    )
    rec = next(e.payload for e in store.read_all() if e.kind == EventKind.RECALL)
    store.close()
    assert rec["returned_claim_ids"] == []
    assert rec["was_used"] is False
    assert rec["abstained"] is False


async def test_recall_log_without_journal_still_acks() -> None:
    hub = _hub(journal=None)
    ws = _WS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="recall_log", query_text="q"), ws)
    assert ws.last()["type"] == "recall_logged"


async def test_recall_log_not_broadcast_to_other_sockets(tmp_path: Path) -> None:
    # Recall telemetry is journalled for the ingest seam, never fanned out as chat.
    hub = _hub(journal=EventStore(tmp_path / "events.db"))
    ws_a = _WS()
    ws_b = _WS()
    await hub.register(ws_a)
    await hub.register(ws_b)
    await hub.handle_message(_msg(sender="A", type="recall_log", query_text="q"), ws_b)
    assert all(json.loads(raw)["type"] != "recall_logged" for raw in ws_a.sent)


def test_record_recall_writes_recall_kind(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    record_recall(store, {"query_text": "q", "by": "A", "at": 1.0})
    events = store.read_all()
    store.close()
    assert events[-1].kind == EventKind.RECALL
    assert events[-1].payload["query_text"] == "q"


def test_replay_ignores_recall_telemetry(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    record_recall(store, {"query_text": "q", "by": "A", "at": 1.0})
    result = replay(store)
    store.close()
    # Recall is memory telemetry, not coordination state — it must not reconstruct any.
    assert result.state.claims == {}
    assert result.chat_history == []


# --- findings (the durable memory spine) -------------------------------------


async def test_finding_journals_durably_with_hub_attested_origin(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = _hub(journal=store)
    ws = _WS()
    await hub.register(ws)
    await hub.handle_message(_finding_msg(), ws)
    records = _findings(store)
    store.close()
    assert len(records) == 1
    rec = records[0]
    assert rec["statement"] == "studio bundle validates byte-parity across federation"
    assert rec["claim_status"] == "reference-validated"  # admitted unchanged
    # Origin is hub-attested, not taken from the client.
    assert rec["provenance"]["actor"] == "AUTHOR"
    assert isinstance(rec["provenance"]["ts"], float)
    assert rec["verified_at_source"]["by"] == "AUTHOR"
    assert isinstance(rec["verified_at_source"]["at"], float)
    # The producer's project survives; an unset validity start is anchored.
    assert rec["provenance"]["project"] == "SCPN-STUDIO"
    # The verdict is broadcast for fleet visibility.
    recorded = next(
        json.loads(r) for r in ws.sent if json.loads(r).get("type") == "finding_recorded"
    )
    assert recorded["verdict"] == "accept"
    assert recorded["claim_status"] == "reference-validated"


async def test_finding_origin_cannot_be_forged(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = _hub(journal=store)
    ws = _WS()
    await hub.register(ws)
    # The sender tries to self-report a different actor and re-check origin.
    await hub.handle_message(
        _finding_msg(
            provenance={"project": "SCPN-STUDIO", "actor": "CEO"},
            verified_at_source={
                "checked_this_session": True,
                "source_ref": "r",
                "by": "CEO",
                "at": 1.0,
            },
        ),
        ws,
    )
    rec = _findings(store)[0]
    store.close()
    assert rec["provenance"]["actor"] == "AUTHOR"  # overwritten by the hub
    assert rec["verified_at_source"]["by"] == "AUTHOR"
    assert rec["verified_at_source"]["at"] != 1.0


async def test_finding_with_unsupported_claim_is_floored(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = _hub(journal=store)
    ws = _WS()
    await hub.register(ws)
    # reference-validated with no evidence_ref -> floored to bounded-support (INV-1).
    await hub.handle_message(_finding_msg(evidence_ref=None), ws)
    rec = _findings(store)[0]
    store.close()
    assert rec["claim_status"] == "bounded-support"
    recorded = next(
        json.loads(r) for r in ws.sent if json.loads(r).get("type") == "finding_recorded"
    )
    assert recorded["verdict"] == "floor"
    assert "INV-1" in recorded["payload"]


async def test_finding_rejected_is_private_and_not_journalled(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = _hub(journal=store)
    ws_a = _WS()
    ws_b = _WS()
    await hub.register(ws_a)
    await hub.register(ws_b)
    await hub.handle_message(_finding_msg(provenance="nope"), ws_a)
    records = _findings(store)
    store.close()
    # Nothing enters the spine.
    assert records == []
    # The sender is privately denied with the reasons.
    denied = next(
        json.loads(r) for r in ws_a.sent if json.loads(r).get("type") == "finding_rejected"
    )
    assert denied["target"] == "AUTHOR"
    assert any("provenance" in reason for reason in denied["reasons"])
    # The rest of the fleet never sees the rejected atom.
    assert all(json.loads(r).get("type") != "finding_rejected" for r in ws_b.sent)
    assert all(json.loads(r).get("type") != "finding_recorded" for r in ws_b.sent)


async def test_finding_recorded_is_broadcast_to_the_fleet(tmp_path: Path) -> None:
    # Unlike recall telemetry, an admitted finding is fanned out so the fleet sees it.
    hub = _hub(journal=EventStore(tmp_path / "events.db"))
    ws_a = _WS()
    ws_b = _WS()
    await hub.register(ws_a)
    await hub.register(ws_b)
    await hub.handle_message(_finding_msg(sender="AUTHOR"), ws_b)
    assert any(json.loads(r).get("type") == "finding_recorded" for r in ws_a.sent)


async def test_finding_without_journal_still_broadcasts() -> None:
    hub = _hub(journal=None)
    ws = _WS()
    await hub.register(ws)
    await hub.handle_message(_finding_msg(), ws)
    assert any(json.loads(r).get("type") == "finding_recorded" for r in ws.sent)


def test_record_finding_writes_finding_kind_durably(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    record_finding(store, {"statement": "x", "claim_status": "bounded-support"})
    events = store.read_all()
    store.close()
    assert events[-1].kind == EventKind.FINDING
    assert events[-1].payload["statement"] == "x"


async def test_memory_tag_is_carried_through_chat_opaquely(tmp_path: Path) -> None:
    # The hub never interprets the tag — it rides the durable chat event and the
    # broadcast unchanged, so a read-side filter can pick out authored context.
    store = EventStore(tmp_path / "events.db")
    hub = _hub(journal=store)
    ws = _WS()
    other = _WS()
    await hub.register(ws)
    await hub.register(other)
    await hub.handle_message(
        _msg(sender="A", type="chat", payload="design rationale", memory_tag="remember"), ws
    )
    chat_events = [e for e in store.read_all() if e.kind == EventKind.CHAT]
    store.close()
    assert chat_events[0].payload["memory_tag"] == "remember"
    fanned = [json.loads(r) for r in other.sent if json.loads(r).get("type") == "chat"]
    assert fanned and fanned[0]["memory_tag"] == "remember"


def test_replay_ignores_findings(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    record_finding(store, {"statement": "x", "claim_status": "bounded-support"})
    result = replay(store)
    store.close()
    # A finding is the durable memory spine, not coordination state.
    assert result.state.claims == {}
    assert result.chat_history == []
