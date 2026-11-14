# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — every stored event carries the hub's own words
"""The describer covers every journal kind, reads each writer's own fields, and the
events feed carries its words while the multihub wire shape stays as it was."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from synapse_channel.core import journal
from synapse_channel.core.event_description import EVENT_WORDS, EventWords, describe_event
from synapse_channel.core.journal import EventKind
from synapse_channel.core.ledger import LedgerTask, ProgressNote
from synapse_channel.core.message_auth_key_watch import MessageAuthKeyAudit
from synapse_channel.core.multihub_wire import LogSnapshot, encode_log_snapshot
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state_models import ResourceOffer, TaskClaim
from synapse_channel.core.token_audit import MetricsTokenAudit, TokenAudit
from synapse_channel.dashboard_store_feeds import build_events_tail


def _kinds() -> set[str]:
    return {value for name, value in vars(EventKind).items() if name.isupper()}


def test_every_journal_kind_has_words_and_nothing_else_does() -> None:
    assert set(EVENT_WORDS) == _kinds()
    assert len(EVENT_WORDS) == 60


def _written(tmp_path: Path) -> list[tuple[str, EventWords | None]]:
    """Write one event per writer family through the hub's own writers, and read it back."""
    store = EventStore(tmp_path / "hub.db")
    claim = TaskClaim(task_id="T-1", owner="P/alice", note="", claimed_at=1.0, lease_expires_at=9.0)
    journal.record_claim(store, claim)
    journal.record_task_update(store, claim)
    journal.record_checkpoint(store, claim)
    journal.record_handoff(store, claim, actor="P/bob")
    journal.record_release(store, "T-1")
    journal.record_resource(store, ResourceOffer(agent="P/alice", kind="llm", name="local"))
    journal.record_role_binding(store, identity="P/alice", roles=("P/reviewer",))
    journal.record_mailbox_watermark(store, identity="P/alice", through_seq=4, source="cursor")
    journal.record_ledger_task(
        store,
        LedgerTask(task_id="T-2", title="t", created_at=1.0, updated_at=1.0, created_by="P/bob"),
    )
    journal.record_ledger_progress(
        store, ProgressNote(task_id="T-2", author="P/bob", kind="note", text="started")
    )
    journal.record_chat(store, {"sender": "P/carol", "target": "all", "payload": "hello"})
    journal.record_claim_denial(
        store, claimant="P/dave", task_id="T-1", reason_code="overlap", worktree="/w", paths=["a"]
    )
    TokenAudit(store).admitted("token-a", "P/erin")
    TokenAudit(store).set_rejected(Path("tokens.json"), "unreadable")
    MetricsTokenAudit(store).expired("metrics-a", 5.0, "watcher")
    MessageAuthKeyAudit(store).retiring_admission("key-old", "P/frank", "key-new", 3600.0)
    store.append("a_kind_of_tomorrow", {"anything": 1})
    events = store.read_since(0, limit=100)
    store.close()
    return [(event.kind, describe_event(event.kind, event.payload)) for event in events]


def test_each_writer_s_own_fields_name_the_actor_and_the_line(tmp_path: Path) -> None:
    words = _written(tmp_path)

    assert words == [
        ("claim", EventWords("P/alice", "claimed T-1")),
        ("task_update", EventWords("P/alice", "updated T-1 (claimed)")),
        ("checkpoint", EventWords("P/alice", "checkpoint T-1")),
        ("handoff", EventWords("", "handed T-1 to P/alice")),
        ("release", EventWords("", "released T-1")),
        ("resource", EventWords("P/alice", "offered llm local")),
        ("role_binding", EventWords("P/alice", "roles: P/reviewer")),
        ("mailbox_watermark", EventWords("P/alice", "mailbox read through 4")),
        ("ledger_task", EventWords("P/bob", "task T-2 (open)")),
        ("ledger_progress", EventWords("P/bob", "T-2: started")),
        ("chat", EventWords("P/carol", "hello")),
        ("claim_denial", EventWords("P/dave", "claim denial: overlap")),
        ("connect_token_admitted", EventWords("P/erin", "admitted by token token-a")),
        ("connect_token_set_rejected", EventWords("", "token set rejected: unreadable")),
        ("metrics_token_expired", EventWords("", "metrics token metrics-a expired")),
        (
            "message_auth_key_retiring_admission",
            EventWords("P/frank", "message signed with retiring key key-old"),
        ),
        ("a_kind_of_tomorrow", None),
    ]


def test_a_payload_missing_its_fields_reads_without_inventing_them() -> None:
    describe = {kind: describe_event(kind, {}) for kind in sorted(_kinds())}

    assert all(words is not None for words in describe.values())
    assert {words.actor for words in describe.values() if words is not None} == {""}
    assert describe["release"] == EventWords("", "released ")
    assert describe["role_binding"] == EventWords("", "roles cleared")
    assert describe["mailbox_watermark"] == EventWords("", "mailbox read")
    assert describe["identity_pin_reclaim"] is not None


def test_long_text_is_cut_and_counts_and_hubs_are_named_when_present() -> None:
    long_chat = describe_event("chat", {"sender": "P/a", "payload": "x" * 200})
    assert long_chat == EventWords("P/a", "x" * 160 + "…")
    forward = describe_event(
        "dead_letter_forwarding",
        {
            "target": "P/b",
            "count": 3,
            "direction": "out",
            "origin_hub_id": "h1",
            "owner_hub_id": "h2",
        },
    )
    assert forward == EventWords("P/b", "dead-letter forward (out): P/b · 3 undelivered · h1 → h2")
    escalation = describe_event("dead_letter_escalation", {"target": "P/b", "count": True})
    assert escalation == EventWords("P/b", "dead-letter escalation: P/b")
    relay = describe_event(
        "operator_relay", {"operator": "op", "action": "release", "task_id": "T"}
    )
    assert relay == EventWords("op", "operator release T (refused)")
    assert describe_event("operator_relay", {"applied": True}) == EventWords(
        "", "operator   (applied)"
    )
    assert describe_event("operator_relay", {"pending": True}) == EventWords(
        "", "operator   (pending)"
    )
    recorded = describe_event("operator_relay", {"status": "not_applied"})
    assert recorded == EventWords("", "operator   (not_applied)")
    finding = describe_event("finding", {"statement": "s", "provenance": {"actor": "P/r"}})
    assert finding == EventWords("P/r", "s")
    assert describe_event("ledger_progress", {"author": "P/a", "text": "t"}) == EventWords(
        "P/a", "t"
    )


def test_the_events_feed_carries_the_words_and_the_wire_does_not(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    journal.record_chat(store, {"sender": "P/carol", "target": "all", "payload": "hello"})
    store.append("a_kind_of_tomorrow", {"anything": 1})
    wire = encode_log_snapshot(LogSnapshot(tuple(store.read_since(0, limit=10)), 2, 2))
    store.close()

    feed = build_events_tail(db)
    events = cast("list[dict[str, Any]]", feed["events"])
    assert (events[0]["actor"], events[0]["summary"]) == ("P/carol", "hello")
    assert "actor" not in events[1] and "summary" not in events[1]
    assert {key for event in wire["events"] for key in event} == {"seq", "ts", "kind", "payload"}
    assert {key: value for key, value in feed.items() if key != "events"} == {
        key: value for key, value in wire.items() if key != "events"
    }


def test_a_receipt_kind_the_projection_gives_no_receipt_reads_as_its_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "synapse_channel.core.event_description.universal_receipt_from_event", lambda _event: None
    )

    assert describe_event("verdict_gate_bypass", {"author": "P/a"}) == EventWords(
        "", "verdict gate bypass"
    )
