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
from synapse_channel.core.journal import EventKind, record_recall, replay
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
