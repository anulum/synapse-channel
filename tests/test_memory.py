# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - end-to-end tests for persistent-memory write-side protocol

from __future__ import annotations

from pathlib import Path
from typing import Any

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, record_finding, record_recall, replay
from synapse_channel.core.persistence import EventStore


def _findings(store: EventStore) -> list[dict[str, Any]]:
    return [e.payload for e in store.read_all() if e.kind == EventKind.FINDING]


async def test_recall_log_journals_with_hub_attested_origin(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(hub_id="syn-test", journal=store)) as (_, uri):
        reason = await connect_agent("REASON", uri)
        try:
            await reason.agent.log_recall(
                "what blocked CONTROL last session?",
                returned_claim_ids=["c1", "c2"],
                was_used=True,
                abstained=False,
            )
            ack = await reason.recorder.wait_for(lambda m: m.get("type") == "recall_logged")
            assert ack["target"] == "REASON"
        finally:
            await close_agents(reason)
    events = [e for e in store.read_all() if e.kind == EventKind.RECALL]
    store.close()
    assert len(events) == 1
    rec = events[0].payload
    assert rec["query_text"] == "what blocked CONTROL last session?"
    assert rec["returned_claim_ids"] == ["c1", "c2"]
    assert rec["was_used"] is True
    assert rec["abstained"] is False
    assert rec["by"] == "REASON"
    assert isinstance(rec["at"], float)


async def test_recall_log_origin_cannot_be_forged(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(hub_id="syn-test", journal=store)) as (_, uri):
        reason = await connect_agent("REASON", uri)
        try:
            await reason.agent.send_message("recall_log", query_text="q", by="CEO", at=1.0)
            await reason.recorder.wait_for(lambda m: m.get("type") == "recall_logged")
        finally:
            await close_agents(reason)
    rec = next(e.payload for e in store.read_all() if e.kind == EventKind.RECALL)
    store.close()
    assert rec["by"] == "REASON"
    assert rec["at"] != 1.0


async def test_recall_log_defaults_and_malformed_ids(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(hub_id="syn-test", journal=store)) as (_, uri):
        alpha = await connect_agent("A", uri)
        try:
            await alpha.agent.send_message("recall_log", query_text="q", returned_claim_ids="oops")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "recall_logged")
        finally:
            await close_agents(alpha)
    rec = next(e.payload for e in store.read_all() if e.kind == EventKind.RECALL)
    store.close()
    assert rec["returned_claim_ids"] == []
    assert rec["was_used"] is False
    assert rec["abstained"] is False


async def test_recall_log_without_journal_still_acks() -> None:
    async with running_hub(SynapseHub(hub_id="syn-test", journal=None)) as (_, uri):
        alpha = await connect_agent("A", uri)
        try:
            await alpha.agent.log_recall("q")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "recall_logged")
        finally:
            await close_agents(alpha)


async def test_recall_log_not_broadcast_to_other_sockets(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(hub_id="syn-test", journal=store)) as (_, uri):
        alpha = await connect_agent("A", uri)
        beta = await connect_agent("B", uri)
        try:
            await beta.agent.log_recall("q")
            await beta.recorder.wait_for(lambda m: m.get("type") == "recall_logged")
            assert all(m.get("type") != "recall_logged" for m in alpha.recorder.messages)
        finally:
            await close_agents(alpha, beta)
    store.close()


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
    assert result.state.claims == {}
    assert result.chat_history == []


async def test_finding_journals_durably_with_hub_attested_origin(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(hub_id="syn-test", journal=store)) as (_, uri):
        author = await connect_agent("AUTHOR", uri)
        try:
            await author.agent.record_finding(
                "studio bundle validates byte-parity across federation",
                subkind="codebase-fact",
                evidence_kind="measured",
                claim_status="reference-validated",
                evidence_ref="tests/test_federation.py:42",
                project="SCPN-STUDIO",
                session="s9",
                valid_from=1.0,
                checked_this_session=True,
                source_ref="federation run",
            )
            recorded = await author.recorder.wait_for(lambda m: m.get("type") == "finding_recorded")
            assert recorded["verdict"] == "accept"
            assert recorded["claim_status"] == "reference-validated"
        finally:
            await close_agents(author)
    records = _findings(store)
    store.close()
    assert len(records) == 1
    rec = records[0]
    assert rec["statement"] == "studio bundle validates byte-parity across federation"
    assert rec["claim_status"] == "reference-validated"
    assert rec["provenance"]["actor"] == "AUTHOR"
    assert isinstance(rec["provenance"]["ts"], float)
    assert rec["verified_at_source"]["by"] == "AUTHOR"
    assert isinstance(rec["verified_at_source"]["at"], float)
    assert rec["provenance"]["project"] == "SCPN-STUDIO"


async def test_finding_origin_cannot_be_forged(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(hub_id="syn-test", journal=store)) as (_, uri):
        author = await connect_agent("AUTHOR", uri)
        try:
            await author.agent.send_message(
                "finding",
                statement="x",
                subkind="codebase-fact",
                evidence_kind="measured",
                claim_status="reference-validated",
                evidence_ref="r",
                provenance={"project": "SCPN-STUDIO", "actor": "CEO"},
                validity={"valid_from": 1.0},
                verified_at_source={
                    "checked_this_session": True,
                    "source_ref": "r",
                    "by": "CEO",
                    "at": 1.0,
                },
            )
            await author.recorder.wait_for(lambda m: m.get("type") == "finding_recorded")
        finally:
            await close_agents(author)
    rec = _findings(store)[0]
    store.close()
    assert rec["provenance"]["actor"] == "AUTHOR"
    assert rec["verified_at_source"]["by"] == "AUTHOR"
    assert rec["verified_at_source"]["at"] != 1.0


async def test_finding_with_unsupported_claim_is_floored(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(hub_id="syn-test", journal=store)) as (_, uri):
        author = await connect_agent("AUTHOR", uri)
        try:
            await author.agent.record_finding(
                "x",
                subkind="codebase-fact",
                evidence_kind="measured",
                claim_status="reference-validated",
                evidence_ref=None,
                project="SCPN-STUDIO",
                checked_this_session=True,
                source_ref="run",
            )
            recorded = await author.recorder.wait_for(lambda m: m.get("type") == "finding_recorded")
            assert recorded["verdict"] == "floor"
            assert "INV-1" in recorded["payload"]
        finally:
            await close_agents(author)
    rec = _findings(store)[0]
    store.close()
    assert rec["claim_status"] == "bounded-support"


async def test_finding_quota_rejects_admitted_memory_without_broadcast(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    async with running_hub(
        SynapseHub(hub_id="syn-test", journal=store, max_findings_per_agent=1)
    ) as (_, uri):
        author = await connect_agent("AUTHOR", uri)
        watcher = await connect_agent("WATCH", uri)
        try:
            for statement in ("first finding", "second finding"):
                await author.agent.record_finding(
                    statement,
                    subkind="codebase-fact",
                    evidence_kind="measured",
                    claim_status="reference-validated",
                    evidence_ref="tests/test_memory.py",
                    project="SYNAPSE-CHANNEL",
                    checked_this_session=True,
                    source_ref="tests/test_memory.py",
                )
            recorded = await author.recorder.wait_for(lambda m: m.get("type") == "finding_recorded")
            rejected = await author.recorder.wait_for(
                lambda m: m.get("type") == "finding_rejected" and "quota" in str(m.get("payload"))
            )
            assert recorded["finding"]["statement"] == "first finding"
            assert rejected["target"] == "AUTHOR"
            assert all(
                not (
                    message.get("type") == "finding_recorded"
                    and message.get("finding", {}).get("statement") == "second finding"
                )
                for message in watcher.recorder.messages
            )
        finally:
            await close_agents(author, watcher)
    records = _findings(store)
    store.close()
    assert [record["statement"] for record in records] == ["first finding"]


async def test_finding_quota_is_seeded_from_replayed_journal(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    record_finding(
        store,
        {
            "statement": "already stored",
            "claim_status": "reference-validated",
            "provenance": {"actor": "AUTHOR"},
        },
    )
    async with running_hub(
        SynapseHub(hub_id="syn-test", journal=store, max_findings_per_agent=1)
    ) as (_, uri):
        author = await connect_agent("AUTHOR", uri)
        try:
            await author.agent.record_finding(
                "after restart",
                subkind="codebase-fact",
                evidence_kind="measured",
                claim_status="reference-validated",
                evidence_ref="tests/test_memory.py",
                project="SYNAPSE-CHANNEL",
                checked_this_session=True,
                source_ref="tests/test_memory.py",
            )
            rejected = await author.recorder.wait_for(lambda m: m.get("type") == "finding_rejected")
            assert "quota" in rejected["payload"]
        finally:
            await close_agents(author)
    records = _findings(store)
    store.close()
    assert [record["statement"] for record in records] == ["already stored"]


async def test_finding_rejected_is_private_and_not_journalled(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(hub_id="syn-test", journal=store)) as (_, uri):
        author = await connect_agent("AUTHOR", uri)
        watcher = await connect_agent("WATCH", uri)
        try:
            await author.agent.send_message(
                "finding",
                statement="x",
                subkind="codebase-fact",
                evidence_kind="measured",
                claim_status="bounded-support",
                provenance="nope",
            )
            denied = await author.recorder.wait_for(lambda m: m.get("type") == "finding_rejected")
            assert denied["target"] == "AUTHOR"
            assert any("provenance" in reason for reason in denied["reasons"])
            assert all(m.get("type") != "finding_rejected" for m in watcher.recorder.messages)
            assert all(m.get("type") != "finding_recorded" for m in watcher.recorder.messages)
        finally:
            await close_agents(author, watcher)
    records = _findings(store)
    store.close()
    assert records == []


async def test_finding_recorded_is_broadcast_to_the_fleet(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(hub_id="syn-test", journal=store)) as (_, uri):
        author = await connect_agent("AUTHOR", uri)
        watcher = await connect_agent("WATCH", uri)
        try:
            await author.agent.record_finding(
                "x",
                subkind="codebase-fact",
                evidence_kind="measured",
                claim_status="bounded-support",
                checked_this_session=True,
                source_ref="r",
            )
            await watcher.recorder.wait_for(lambda m: m.get("type") == "finding_recorded")
        finally:
            await close_agents(author, watcher)
    store.close()


async def test_finding_without_journal_still_broadcasts() -> None:
    async with running_hub(SynapseHub(hub_id="syn-test", journal=None)) as (_, uri):
        author = await connect_agent("AUTHOR", uri)
        try:
            await author.agent.record_finding(
                "x",
                subkind="codebase-fact",
                evidence_kind="measured",
                claim_status="bounded-support",
                checked_this_session=True,
                source_ref="r",
            )
            await author.recorder.wait_for(lambda m: m.get("type") == "finding_recorded")
        finally:
            await close_agents(author)


def test_record_finding_writes_finding_kind_durably(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    record_finding(store, {"statement": "x", "claim_status": "bounded-support"})
    events = store.read_all()
    store.close()
    assert events[-1].kind == EventKind.FINDING
    assert events[-1].payload["statement"] == "x"


async def test_memory_tag_is_carried_through_chat_opaquely(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(hub_id="syn-test", journal=store)) as (_, uri):
        author = await connect_agent("A", uri)
        watcher = await connect_agent("WATCH", uri)
        try:
            await author.agent.chat("design rationale", target="all", memory_tag="remember")
            fanned = await watcher.recorder.wait_for(lambda m: m.get("type") == "chat")
            assert fanned["memory_tag"] == "remember"
        finally:
            await close_agents(author, watcher)
    chat_events = [e for e in store.read_all() if e.kind == EventKind.CHAT]
    store.close()
    assert chat_events[0].payload["memory_tag"] == "remember"


def test_replay_ignores_findings(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    record_finding(store, {"statement": "x", "claim_status": "bounded-support"})
    result = replay(store)
    store.close()
    assert result.state.claims == {}
    assert result.chat_history == []
