# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — forward event feed tests through a real hub
"""A memory consumer reads findings and marked chat, bound to the hub and journal instance."""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import run_cli
from hub_e2e_helpers import (
    AgentHandle,
    Recorder,
    close_agents,
    connect_agent,
    free_port,
    running_hub,
)
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.acl import FEED_READ, WOULD_DENY, AclPolicy, AclRule
from synapse_channel.core.feed import FEED_PAGE_BYTES, payload_sha256
from synapse_channel.core.feed_retention import RETENTION_SCAN_PAGE
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, record_finding
from synapse_channel.core.persistence import EventStore
from synapse_channel.hub_feed import FeedUnavailable, request_feed_page, send_feed_ack
from synapse_channel.hub_inbox import INBOX_READY_HANG_GUARD_SECONDS

_WAIT = INBOX_READY_HANG_GUARD_SECONDS
_HUB = "feed-hub"


async def _page(handle: AgentHandle, **fields: Any) -> dict[str, Any]:
    """Send one feed query from ``handle`` and return its page."""
    request_id = uuid.uuid4().hex
    query = {
        "version": 1,
        "consumer": handle.agent.name,
        "hub_id": "",
        "journal_instance": "",
        "after_seq": 0,
        "kinds": ["finding", "chat_marker"],
        "limit": 100,
        **fields,
    }
    await handle.agent.send_message(
        "history_request", target="System", request_id=request_id, feed_query=query
    )
    frame = await handle.recorder.wait_for(
        lambda m: m.get("type") == "history_snapshot" and m.get("request_id") == request_id,
        timeout=_WAIT,
    )
    page: dict[str, Any] = frame["feed_page"]
    return page


async def _ack(
    handle: AgentHandle, page: dict[str, Any], acked_seq: int, **fields: Any
) -> dict[str, Any]:
    """Acknowledge ``acked_seq`` against the cursor of ``page`` and return the hub's record."""
    request_id = uuid.uuid4().hex
    frame_fields = {
        "consumer": handle.agent.name,
        "hub_id": page["hub_id"],
        "journal_instance": page["journal_instance"],
        "acked_seq": acked_seq,
        **fields,
    }
    await handle.agent.send_message(
        "feed_ack", target="System", request_id=request_id, **frame_fields
    )
    frame = await handle.recorder.wait_for(
        lambda m: m.get("type") == "feed_ack_result" and m.get("request_id") == request_id,
        timeout=_WAIT,
    )
    result: dict[str, Any] = frame["feed_ack"]
    return result


async def _fence(handle: AgentHandle) -> None:
    """Wait until the hub applied this connection's earlier sends (a who reply proves it)."""
    handle.recorder.messages.clear()
    await handle.agent.request_who()
    await handle.recorder.wait_for(lambda m: m.get("type") == "who_snapshot", timeout=_WAIT)


def _seed(journal: EventStore) -> list[int]:
    """Journal two findings and a channel-tagged marker chat before the hub starts."""
    first = record_finding_seq(journal, {"text": "the cache is cold", "nested": {"b": 2, "a": "ä"}})
    journal.append(
        EventKind.CHAT,
        {
            "type": "chat",
            "sender": "R/pub",
            "target": "all",
            "channel": "ops",
            "payload": "Finding: in a channel",
        },
    )
    second = record_finding_seq(journal, {"text": "we chose worktrees"})
    return [first, second]


def record_finding_seq(journal: EventStore, record: dict[str, Any]) -> int:
    """Record a finding through the journal's own writer and return its sequence."""
    record_finding(journal, record)
    return journal.max_seq()


async def test_a_page_serves_findings_and_undirected_marked_chat_only(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "hub.db")
    findings = _seed(journal)
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (hub, uri):
            publisher = await connect_agent("R/pub", uri)
            consumer = await connect_agent("R/consumer", uri)
            try:
                await publisher.agent.chat("Decision: keep the floor")
                await publisher.agent.chat("Finding: directed", target="R/consumer")
                await publisher.agent.chat("an ordinary remark")
                await _fence(publisher)

                page = await _page(consumer)
                again = await _page(consumer)
            finally:
                await close_agents(publisher, consumer)
        instance = journal.journal_instance()
    finally:
        journal.close()

    assert page["available"] is True and page["hub_id"] == hub.hub_id
    assert instance is not None and page["journal_instance"] == instance.instance
    rows = page["rows"]
    assert [row["kind"] for row in rows] == ["finding", "finding", "chat_marker"]
    assert [row["seq"] for row in rows[:2]] == findings
    assert rows[2]["payload"]["payload"] == "Decision: keep the floor"
    assert [row["seq"] for row in rows] == sorted(row["seq"] for row in rows)
    assert all(row["payload_sha256"] == payload_sha256(row["payload"]) for row in rows)
    assert rows[0]["payload"]["nested"] == {"a": "ä", "b": 2}
    assert page["next_after"] == page["max_seq"] and page["has_more"] is False
    assert again["rows"] == rows  # a page is a pure read


async def test_pages_are_bounded_and_the_cursor_passes_unserved_rows(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "hub.db")
    for index in range(3):
        record_finding(journal, {"text": f"finding {index}"})
        journal.append(
            EventKind.CHAT, {"type": "chat", "sender": "R/pub", "target": "all", "payload": "plain"}
        )
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            consumer = await connect_agent("R/consumer", uri)
            try:
                first = await _page(consumer, kinds=["finding"], limit=1)
                bound = {"hub_id": first["hub_id"], "journal_instance": first["journal_instance"]}
                second = await _page(
                    consumer, kinds=["finding"], limit=1, after_seq=first["next_after"], **bound
                )
                markers = await _page(consumer, kinds=["chat_marker"], limit=5)
            finally:
                await close_agents(consumer)
    finally:
        journal.close()

    assert [row["payload"]["text"] for row in first["rows"]] == ["finding 0"]
    assert first["has_more"] is True
    assert [row["payload"]["text"] for row in second["rows"]] == ["finding 1"]
    assert markers["rows"] == [] and markers["next_after"] == markers["max_seq"]


async def test_a_cursor_is_bound_to_the_hub_and_the_journal_instance(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "hub.db")
    _seed(journal)
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            consumer = await connect_agent("R/consumer", uri)
            try:
                first = await _page(consumer)
                bound = {"hub_id": first["hub_id"], "journal_instance": first["journal_instance"]}
                other_hub = await _page(
                    consumer, hub_id="another-hub", journal_instance=first["journal_instance"]
                )
                other_instance = await _page(
                    consumer, hub_id=first["hub_id"], journal_instance="f" * 32
                )
                ahead = await _page(consumer, after_seq=first["max_seq"] + 1, **bound)
                unbound_ahead = await _page(consumer, after_seq=1)
            finally:
                await close_agents(consumer)
    finally:
        journal.close()

    assert "different hub" in other_hub["error"] and other_hub["rows"] == []
    assert other_instance["available"] is False and other_instance["rows"] == []
    assert other_instance["source_changed"] == {
        "journal_instance": first["journal_instance"],
        "instance_floor": first["instance_floor"],
    }
    assert "exceeds the retained journal" in ahead["error"]
    assert "unbound feed query starts at sequence 0" in unbound_ahead["error"]


async def test_kinds_are_closed_and_the_consumer_owns_the_connection(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "hub.db")
    _seed(journal)
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            consumer = await connect_agent("R/consumer", uri)
            try:
                chat = await _page(consumer, kinds=["chat"])
                foreign = await _page(consumer, consumer="R/someone-else")
                malformed = await _page(consumer, limit=0)
            finally:
                await close_agents(consumer)
    finally:
        journal.close()

    assert "unsupported feed kind" in chat["error"] and chat["rows"] == []
    assert "not this connection" in foreign["error"]
    assert malformed["error"] == "invalid feed query"


async def test_every_malformed_field_of_a_query_is_refused(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "hub.db")
    _seed(journal)
    malformed: tuple[dict[str, Any], ...] = (
        {"consumer": ""},
        {"consumer": "   "},
        {"consumer": 7},
        {"hub_id": 7},
        {"journal_instance": None},
        {"after_seq": -1},
        {"after_seq": True},
        {"limit": 0},
        {"kinds": []},
        {"kinds": "finding"},
        {"kinds": [7]},
        {"kinds": ["finding", "finding"]},
    )
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            consumer = await connect_agent("R/consumer", uri)
            try:
                pages = [await _page(consumer, **fields) for fields in malformed]
            finally:
                await close_agents(consumer)
    finally:
        journal.close()

    assert [(page["error"], page["rows"]) for page in pages] == [("invalid feed query", [])] * len(
        malformed
    )


async def _refused(handle: AgentHandle, msg_type: str, **fields: Any) -> str:
    """Send one raw frame from ``handle`` and return the text of the hub's error reply.

    An access-control refusal names no request id, so the recorder is cleared
    first and the next error is this frame's.
    """
    handle.recorder.messages.clear()
    await handle.agent.send_message(msg_type, target="System", **fields)
    frame = await handle.recorder.wait_for(lambda m: m.get("type") == "error", timeout=_WAIT)
    assert frame["acl_decision"] == WOULD_DENY
    text: str = frame["payload"]
    return text


def _query(consumer: str, *kinds: str) -> dict[str, Any]:
    """Return an unbound first feed query for ``consumer``."""
    return {
        "version": 1,
        "consumer": consumer,
        "hub_id": "",
        "journal_instance": "",
        "after_seq": 0,
        "kinds": list(kinds),
        "limit": 10,
    }


async def test_the_feed_read_grant_is_per_kind_and_per_namespace(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "hub.db")
    _seed(journal)
    policy = AclPolicy([AclRule(FEED_READ, "feed", "finding", namespace="R")])
    hub = SynapseHub(journal=journal, hub_id=_HUB, acl_policy=policy, require_acl=True)
    try:
        async with running_hub(hub) as (_, uri):
            consumer = await connect_agent("R/consumer", uri)
            outsider = await connect_agent("Q/outsider", uri)
            try:
                granted = await _page(consumer, kinds=["finding"])
                marker = await _refused(
                    consumer, "history_request", feed_query=_query("R/consumer", "chat_marker")
                )
                both = await _refused(
                    consumer,
                    "history_request",
                    feed_query=_query("R/consumer", "finding", "chat_marker"),
                )
                ack = await _refused(
                    consumer,
                    "feed_ack",
                    consumer="R/consumer",
                    hub_id=_HUB,
                    journal_instance=granted["journal_instance"],
                    acked_seq=granted["next_after"],
                )
                foreign = await _refused(
                    outsider, "history_request", feed_query=_query("Q/outsider", "finding")
                )
                no_kinds = await _refused(
                    consumer, "history_request", feed_query=_query("R/consumer")
                )
                # a frame is authorised for the query the hub answers: the inbox, not the feed
                mixed = await _refused(
                    consumer,
                    "history_request",
                    feed_query=_query("R/consumer", "finding"),
                    inbox_query={
                        "version": 1,
                        "identity": "R/consumer",
                        "hub_id": "",
                        "since_seq": 0,
                        "limit": 10,
                    },
                )
            finally:
                await close_agents(consumer, outsider)
            cli = await asyncio.to_thread(
                run_cli,
                "feed",
                "read",
                "--uri",
                uri,
                "--name",
                "Q/cli",
                "--consumer",
                "Q/cli",
                "--kind",
                "finding",
            )
    finally:
        journal.close()

    assert cli.returncode == 1, cli.stderr
    assert json.loads(cli.stdout)["error"] == "access denied: feed-read on feed:finding"

    assert granted["available"] is True
    assert [row["kind"] for row in granted["rows"]] == ["finding", "finding"]
    assert marker == "access denied: feed-read on feed:chat_marker"
    assert both == "access denied: feed-read on feed:chat_marker"
    assert ack == "access denied: feed-read on feed:ack"
    assert foreign == "access denied: feed-read on feed:finding"
    assert no_kinds == "access denied: feed-read on feed:"
    assert mixed == "access denied: mailbox on agent:R/consumer"


async def test_a_role_holder_reads_and_acknowledges_as_its_role(tmp_path: Path) -> None:
    role = "R/role/memory-consumer"
    journal = EventStore(tmp_path / "hub.db")
    _seed(journal)
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            recorder = Recorder()
            agent = SynapseAgent(
                "R/holder", recorder, uri=uri, heartbeat_interval=60.0, verbose=False, roles=(role,)
            )
            holder = AgentHandle(
                agent=agent, recorder=recorder, task=asyncio.create_task(agent.connect())
            )
            assert await agent.wait_until_ready(3.0)
            other = await connect_agent("R/other", uri)
            try:
                page = await _page(holder, consumer=role)
                acked = await _ack(holder, page, page["next_after"], consumer=role)
                not_its_role = await _page(other, consumer=role)
                not_its_ack = await _ack(other, page, page["next_after"], consumer=role)
            finally:
                await close_agents(holder, other)
    finally:
        journal.close()

    assert page["consumer"] == role and len(page["rows"]) == 2
    assert (acked["recorded"], acked["consumer"]) == (True, role)
    assert not_its_role["error"] == "feed consumer is not this connection or one of its roles"
    assert not_its_ack["error"] == "feed consumer is not this connection or one of its roles"


async def test_an_ack_is_durable_monotone_and_never_ahead_of_the_pages_served(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    journal = EventStore(db)
    _seed(journal)
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            consumer = await connect_agent("R/consumer", uri)
            try:
                probe = await _page(consumer, kinds=["finding"], limit=1)
                served = probe["next_after"]
                ahead = await _ack(consumer, probe, served + 1)
                recorded = await _ack(consumer, probe, served)
                repeated = await _ack(consumer, probe, served)
                lower = await _ack(consumer, probe, served - 1)
                wrong_instance = await _ack(consumer, probe, served, journal_instance="0" * 32)
                wrong_hub = await _ack(consumer, probe, served, hub_id="another-hub")
                malformed = await _ack(consumer, probe, -1)
            finally:
                await close_agents(consumer)
    finally:
        journal.close()

    assert "ahead of the pages served" in ahead["error"]
    assert (recorded["recorded"], recorded["acked_seq"]) == (True, served)
    assert (repeated["recorded"], repeated["acked_seq"]) == (False, served)
    assert (lower["recorded"], lower["acked_seq"]) == (False, served)
    assert "different journal instance" in wrong_instance["error"]
    assert "different hub" in wrong_hub["error"]
    assert malformed["error"] == "invalid feed acknowledgement"
    with closing(sqlite3.connect(db)) as connection:
        acks = connection.execute("SELECT payload FROM events WHERE kind = 'feed_ack'").fetchall()
    assert [json.loads(row[0])["acked_seq"] for row in acks] == [served]

    restarted = EventStore(db)
    try:
        async with running_hub(SynapseHub(journal=restarted, hub_id=_HUB)) as (_, uri):
            consumer = await connect_agent("R/consumer", uri)
            try:
                before_any_page = await _ack(consumer, probe, served + 1)
                page = await _page(consumer)
                kept = await _ack(consumer, page, served - 1)
            finally:
                await close_agents(consumer)
    finally:
        restarted.close()

    assert "ahead of the pages served" in before_any_page["error"]  # served is per process
    assert (kept["recorded"], kept["acked_seq"]) == (False, served)  # read back from the journal


async def test_a_degraded_hub_still_serves_a_corrupt_finding_and_keeps_the_ack_past_it(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    journal = EventStore(db)
    first, second = _seed(journal)
    journal.close()
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("UPDATE events SET payload = '{' WHERE seq = ?", (first,))
        connection.commit()
    journal = EventStore(db)
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            consumer = await connect_agent("R/consumer", uri)
            try:
                page = await _page(consumer, kinds=["finding"])
                # the hub refuses mutations until the corrupt prefix is settled; settling needs acks
                await consumer.agent.chat("Finding: not while degraded")
                refused = await consumer.recorder.wait_for(
                    lambda m: m.get("journal_recovery_required") is True, timeout=_WAIT
                )
                acked = await _ack(consumer, page, page["next_after"])
            finally:
                await close_agents(consumer)
    finally:
        journal.close()

    assert page["rows"][0] == {"seq": first, "kind": "finding", "corrupt": True}
    assert page["rows"][1]["seq"] == second and "payload" in page["rows"][1]
    assert refused["first_corrupt_seq"] == first
    assert (acked["recorded"], acked["acked_seq"]) == (True, second)


async def test_a_hub_without_a_journal_or_a_configured_id_refuses_the_feed(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "hub.db")
    _seed(journal)
    refusals: list[str] = []
    try:
        for hub in (SynapseHub(hub_id=_HUB), SynapseHub(journal=journal)):
            async with running_hub(hub) as (_, uri):
                consumer = await connect_agent("R/consumer", uri)
                try:
                    page = await _page(consumer)
                    ack = await _ack(consumer, {"hub_id": hub.hub_id, "journal_instance": ""}, 0)
                finally:
                    await close_agents(consumer)
            assert page["rows"] == [] and page["available"] is False
            refusals += [page["error"], ack["error"]]
    finally:
        journal.close()

    assert refusals == ["the feed requires a durable journal and a configured hub id"] * 4


def test_the_digest_is_canonical_json_and_refuses_a_non_finite_number() -> None:
    payload = {"b": [1, {"y": "ü", "x": None}], "a": "é"}
    expected = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert (
        payload_sha256(payload)
        == __import__("hashlib").sha256(expected.encode("utf-8")).hexdigest()
    )
    with pytest.raises(ValueError):
        payload_sha256({"x": math.nan})


async def test_the_cli_reads_a_page_and_acknowledges_it(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "hub.db")
    _seed(journal)
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            read = await asyncio.to_thread(
                run_cli,
                "feed",
                "read",
                "--uri",
                uri,
                "--name",
                "R/cli",
                "--consumer",
                "R/cli",
                "--kind",
                "finding",
                "--limit",
                "1",
            )
            first = json.loads(read.stdout)
            following = await asyncio.to_thread(
                run_cli,
                "feed",
                "read",
                "--uri",
                uri,
                "--name",
                "R/cli",
                "--consumer",
                "R/cli",
                "--kind",
                "finding",
                "--after",
                str(first["next_after"]),
                "--hub-id",
                first["hub_id"],
                "--instance",
                first["journal_instance"],
            )
            page = json.loads(following.stdout)
            ack = await asyncio.to_thread(
                run_cli,
                "feed",
                "ack",
                "--uri",
                uri,
                "--name",
                "R/cli",
                "--consumer",
                "R/cli",
                "--hub-id",
                page["hub_id"],
                "--instance",
                page["journal_instance"],
                "--seq",
                str(page["next_after"]),
            )
            refused = await asyncio.to_thread(
                run_cli,
                "feed",
                "ack",
                "--uri",
                uri,
                "--name",
                "R/cli",
                "--consumer",
                "R/cli",
                "--hub-id",
                page["hub_id"],
                "--instance",
                page["journal_instance"],
                "--seq",
                str(page["next_after"] + 1),
            )
    finally:
        journal.close()

    assert read.returncode == 0 and following.returncode == 0, read.stderr + following.stderr
    assert [row["payload"]["text"] for row in first["rows"]] == ["the cache is cold"]
    assert [row["payload"]["text"] for row in page["rows"]] == ["we chose worktrees"]
    assert ack.returncode == 0 and json.loads(ack.stdout)["recorded"] is True
    assert (
        refused.returncode == 1
        and "ahead of the pages served" in json.loads(refused.stdout)["error"]
    )


def _api_snippet() -> str:
    """Return the feed client example of the API reference, as a reader pastes it."""
    doc = (Path(__file__).resolve().parents[1] / "docs" / "api.md").read_text(encoding="utf-8")
    section = doc.split("## Forward event feed client\n", 1)[1]
    return section.split("```python\n", 1)[1].split("```", 1)[0]


async def test_the_api_reference_example_reads_and_acknowledges(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    journal = EventStore(db)
    _seed(journal)
    namespace: dict[str, Any] = {}
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            exec(_api_snippet().replace("ws://localhost:8876", uri), namespace)
            first = await namespace["read_once"]({})
            second = await namespace["read_once"](first)
        instance = journal.journal_instance()
    finally:
        journal.close()

    assert instance is not None
    assert first == {"after_seq": 3, "hub_id": _HUB, "journal_instance": instance.instance}
    assert second == first
    with closing(sqlite3.connect(db)) as connection:
        acks = connection.execute("SELECT payload FROM events WHERE kind = 'feed_ack'").fetchall()
    assert [json.loads(row[0])["acked_seq"] for row in acks] == [3]


async def test_damaged_rows_are_served_as_markers_or_passed(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    journal = EventStore(db)
    marked = journal.append(
        EventKind.CHAT,
        {"type": "chat", "sender": "R/pub", "target": "all", "payload": "Finding: x"},
    )
    finding = record_finding_seq(journal, {"text": "a finding"})
    journal.close()
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("UPDATE events SET payload = '{' WHERE seq = ?", (marked,))
        connection.execute("UPDATE events SET payload = '{\"x\": NaN}' WHERE seq = ?", (finding,))
        connection.commit()
    journal = EventStore(db)
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            consumer = await connect_agent("R/consumer", uri)
            try:
                page = await _page(consumer)
                version_two = await _page(consumer, version=2)
            finally:
                await close_agents(consumer)
    finally:
        journal.close()

    # an undecodable chat cannot show whether it was marked; a non-finite payload has no digest
    assert page["rows"] == [{"seq": finding, "kind": "finding", "corrupt": True}]
    assert page["next_after"] == finding
    assert version_two["error"] == "invalid feed query"


async def test_the_page_byte_budget_stops_a_page_and_refuses_one_oversized_row(
    tmp_path: Path,
) -> None:
    journal = EventStore(tmp_path / "hub.db")
    half = "x" * (FEED_PAGE_BYTES // 2 + 1)
    first = record_finding_seq(journal, {"text": half})
    second = record_finding_seq(journal, {"text": half})
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            consumer = await connect_agent("R/consumer", uri)
            try:
                page = await _page(consumer, kinds=["finding"])
                record_finding(journal, {"text": "x" * (FEED_PAGE_BYTES + 1)})
                bound = {"hub_id": page["hub_id"], "journal_instance": page["journal_instance"]}
                rest = await _page(consumer, kinds=["finding"], after_seq=first, **bound)
                oversized = await _page(consumer, kinds=["finding"], after_seq=second, **bound)
            finally:
                await close_agents(consumer)
    finally:
        journal.close()

    assert [row["seq"] for row in page["rows"]] == [first]
    assert (page["next_after"], page["has_more"]) == (first, True)
    assert [row["seq"] for row in rest["rows"]] == [second]
    assert oversized["error"] == "a feed row exceeds the response size limit"
    assert oversized["next_after"] == second


async def test_a_kept_ack_is_read_back_past_other_consumers_and_foreign_rows(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    journal = EventStore(db)
    _seed(journal)
    instance = journal.journal_instance()
    assert instance is not None
    ack = {"hub_id": _HUB, "journal_instance": instance.instance}
    # more acks than one scan reads, the highest last; another consumer's acks interleave
    journal.append_batch(
        [
            (EventKind.FEED_ACK, {"consumer": "R/consumer", **ack, "acked_seq": 1})
            for _ in range(RETENTION_SCAN_PAGE)
        ]
        + [
            (EventKind.FEED_ACK, {"consumer": "R/other", **ack, "acked_seq": 3}),
            (EventKind.FEED_ACK, {"consumer": "R/consumer", **ack, "acked_seq": "3"}),
            (EventKind.FEED_ACK, {"consumer": "R/consumer", **ack, "acked_seq": 2}),
        ]
    )
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            consumer = await connect_agent("R/consumer", uri)
            try:
                page = await _page(consumer, kinds=["finding"], limit=2)
                below = await _ack(consumer, page, 2)
                above = await _ack(consumer, page, page["next_after"])
            finally:
                await close_agents(consumer)
    finally:
        journal.close()

    assert page["next_after"] == 3
    assert (below["recorded"], below["acked_seq"]) == (False, 2)
    assert (above["recorded"], above["acked_seq"]) == (True, 3)


async def test_a_record_that_lost_its_instance_binds_no_cursor(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    journal = EventStore(db)
    _seed(journal)
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            consumer = await connect_agent("R/consumer", uri)
            try:
                first = await _page(consumer)
                with closing(sqlite3.connect(db)) as connection:
                    connection.execute("UPDATE store_format SET journal_instance = NULL")
                    connection.commit()
                page = await _page(consumer)
                ack = await _ack(consumer, first, first["next_after"])
            finally:
                await close_agents(consumer)
    finally:
        journal.close()

    assert page["error"] == "the journal records no instance; a feed cursor cannot be bound"
    assert (page["journal_instance"], page["rows"]) == ("", [])
    assert ack["error"] == "feed acknowledgement names a different journal instance"


@pytest.mark.parametrize("hub_answer", ["silence", "no_page", "error_twice"])
async def test_the_client_names_a_hub_that_answers_wrongly_or_not_at_all(hub_answer: str) -> None:
    from websockets.asyncio.server import ServerConnection, serve

    async def endpoint(socket: ServerConnection) -> None:
        await socket.send(json.dumps({"type": "welcome", "hub_id": "odd-hub"}))
        async for raw in socket:
            request = json.loads(raw)
            if request.get("type") == "who_request":
                await socket.send(json.dumps({"type": "who_snapshot", "agents": []}))
            if request.get("type") != "history_request":
                continue
            if hub_answer == "no_page":
                reply = {"type": "history_snapshot", "request_id": request["request_id"]}
                await socket.send(json.dumps(reply))
            elif hub_answer == "error_twice":
                for text in ("access denied: feed-read on feed:finding", "a second error"):
                    await socket.send(
                        json.dumps({"type": "error", "payload": text, "acl_reason": 5})
                    )

    async with serve(endpoint, "127.0.0.1", 0) as server:
        uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        call = request_feed_page(
            uri=uri,
            identity="R/reader",
            token=None,
            consumer="R/reader",
            kinds=["finding"],
            timeout=1.0,
        )
        if hub_answer == "error_twice":
            assert await call == {"error": "access denied: feed-read on feed:finding"}
            return
        with pytest.raises(FeedUnavailable) as raised:
            await call
    expected = "gave no answer in 1.0 s" if hub_answer == "silence" else "carries no feed_page"
    assert expected in str(raised.value)


async def test_an_unreachable_hub_is_unavailable_and_the_cli_exits_two() -> None:
    uri = f"ws://127.0.0.1:{free_port()}"
    with pytest.raises(FeedUnavailable, match="did not accept R/reader"):
        await send_feed_ack(
            uri=uri,
            identity="R/reader",
            token=None,
            consumer="R/reader",
            hub_id=_HUB,
            journal_instance="0" * 32,
            acked_seq=1,
            timeout=0.5,
        )
    connection = ("--uri", uri, "--name", "R/cli", "--consumer", "R/cli", "--timeout", "0.5")
    read = await asyncio.to_thread(run_cli, "feed", "read", *connection, "--kind", "finding")
    ack = await asyncio.to_thread(
        run_cli, "feed", "ack", *connection, "--hub-id", _HUB, "--instance", "0" * 32, "--seq", "1"
    )
    limit = await asyncio.to_thread(
        run_cli, "feed", "read", *connection, "--kind", "finding", "--limit", "501"
    )

    assert (read.returncode, read.stdout) == (2, "")
    assert read.stderr.startswith("feed read: the hub at ")
    assert (ack.returncode, ack.stdout) == (2, "")
    assert ack.stderr.startswith("feed ack: the hub at ")
    assert limit.returncode == 2 and "--limit must be 1 to 500" in limit.stderr
