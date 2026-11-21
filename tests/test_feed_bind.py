# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — binding a legacy feed position by proof, through a real hub
"""A consumer with a sequence-only position binds once, by the proof of its last row."""

from __future__ import annotations

import asyncio
import json
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
from synapse_channel.core.acl import FEED_READ, AclPolicy, AclRule
from synapse_channel.core.feed import FEED_SCAN_LIMIT, LEGACY_BINDING_UNVERIFIED, payload_sha256
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, record_finding
from synapse_channel.core.persistence import EventStore
from synapse_channel.hub_feed import FeedUnavailable, send_feed_bind
from synapse_channel.hub_inbox import INBOX_READY_HANG_GUARD_SECONDS

_WAIT = INBOX_READY_HANG_GUARD_SECONDS
_HUB = "feed-hub"
_ROLES = tuple(f"R/role/consumer-{index}" for index in range(12))


async def _holder(uri: str, name: str = "R/holder") -> AgentHandle:
    """Connect one identity that answers to every role in :data:`_ROLES`."""
    recorder = Recorder()
    agent = SynapseAgent(
        name, recorder, uri=uri, heartbeat_interval=60.0, verbose=False, roles=_ROLES
    )
    handle = AgentHandle(agent=agent, recorder=recorder, task=asyncio.create_task(agent.connect()))
    if not await agent.wait_until_ready(3.0):
        await handle.close()
        raise TimeoutError(f"{name} did not receive the hub welcome")
    return handle


async def _reply(handle: AgentHandle, msg_type: str, reply_type: str, **fields: Any) -> Any:
    """Send one frame and return its correlated reply."""
    request_id = uuid.uuid4().hex
    await handle.agent.send_message(msg_type, target="System", request_id=request_id, **fields)
    return await handle.recorder.wait_for(
        lambda m: m.get("type") == reply_type and m.get("request_id") == request_id,
        timeout=_WAIT,
    )


async def _bind(handle: AgentHandle, consumer: str, **fields: Any) -> dict[str, Any]:
    """Send one ``feed_bind`` for ``consumer`` and return the hub's record of it."""
    frame = await _reply(handle, "feed_bind", "feed_bind_result", consumer=consumer, **fields)
    result: dict[str, Any] = frame["feed_bind"]
    return result


async def _page(handle: AgentHandle, consumer: str, **fields: Any) -> dict[str, Any]:
    """Read one feed page of findings for ``consumer``."""
    query = {
        "version": 1,
        "consumer": consumer,
        "hub_id": "",
        "journal_instance": "",
        "after_seq": 0,
        "kinds": ["finding"],
        "limit": 100,
        **fields,
    }
    frame = await _reply(handle, "history_request", "history_snapshot", feed_query=query)
    page: dict[str, Any] = frame["feed_page"]
    return page


def _finding(journal: EventStore, text: str) -> int:
    """Record one finding and return its sequence."""
    record_finding(journal, {"text": text, "note": "ä"})
    return journal.max_seq()


def _digest(journal: EventStore, seq: int) -> str:
    """Return the digest the feed serves for the row at ``seq``."""
    (event,) = journal.read_since(seq - 1, limit=1)
    return payload_sha256(event.payload)


async def test_a_kept_sequence_and_its_digest_bind_the_legacy_cursor(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    journal = EventStore(db)
    first = _finding(journal, "first")
    journal.append(
        EventKind.CHAT, {"type": "chat", "sender": "R/pub", "target": "all", "payload": "x"}
    )
    second = _finding(journal, "second")
    third = _finding(journal, "third")
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            holder = await _holder(uri)
            try:
                served = await _page(holder, _ROLES[0])
                proof = {
                    "kind": "finding",
                    "seq": second,
                    "payload_sha256": served["rows"][1]["payload_sha256"],
                }
                bound = await _bind(holder, _ROLES[1], legacy_seq=second, proof=proof)
                scanned_past = await _bind(holder, _ROLES[2], legacy_seq=third, proof=proof)
                cursor = {"hub_id": bound["hub_id"], "journal_instance": bound["journal_instance"]}
                after = await _page(holder, _ROLES[1], after_seq=bound["after_seq"], **cursor)
            finally:
                await close_agents(holder)
        instance = journal.journal_instance()
    finally:
        journal.close()

    assert instance is not None
    assert served["rows"][1]["seq"] == second  # the digest a page serves is the one a bind checks
    assert bound == {
        "version": 1,
        "bound": True,
        "consumer": _ROLES[1],
        "hub_id": _HUB,
        "journal_instance": instance.instance,
        "after_seq": second,
    }
    assert (scanned_past["bound"], scanned_past["after_seq"]) == (True, third)
    assert [row["seq"] for row in after["rows"]] == [third]
    assert first < second
    with closing(sqlite3.connect(db)) as connection:
        rows = connection.execute("SELECT payload FROM events WHERE kind = 'feed_bind'").fetchall()
    records = [json.loads(row[0]) for row in rows]
    assert [(r["consumer"], r["after_seq"], r["proof_seq"]) for r in records] == [
        (_ROLES[1], second, second),
        (_ROLES[2], third, second),
    ]
    assert all(
        r["proof_kind"] == "finding" and r["journal_instance"] == instance.instance for r in records
    )


async def test_a_proof_the_feed_would_not_serve_binds_nothing(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    journal = EventStore(db)
    finding = _finding(journal, "a finding")
    plain = journal.append(
        EventKind.CHAT, {"type": "chat", "sender": "R/pub", "target": "all", "payload": "plain"}
    )
    directed = journal.append(
        EventKind.CHAT,
        {"type": "chat", "sender": "R/pub", "target": "R/other", "payload": "Finding: private"},
    )
    corrupt = _finding(journal, "to be damaged")
    corrupt_digest = _digest(journal, corrupt)
    directed_digest = _digest(journal, directed)
    good = _digest(journal, finding)
    journal.close()
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("UPDATE events SET payload = '{' WHERE seq = ?", (corrupt,))
        connection.commit()
    journal = EventStore(db)

    def proof(kind: str, seq: int | None, digest: str) -> dict[str, Any]:
        body: dict[str, Any] = {"kind": kind, "payload_sha256": digest}
        if seq is not None:
            body["seq"] = seq
        return body

    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            holder = await _holder(uri)
            try:
                results = [
                    await _bind(
                        holder,
                        _ROLES[0],
                        legacy_seq=finding,
                        proof=proof("finding", finding, "0" * 64),
                    ),
                    await _bind(
                        holder, _ROLES[1], legacy_seq=plain, proof=proof("finding", plain, good)
                    ),
                    await _bind(
                        holder,
                        _ROLES[2],
                        legacy_seq=directed,
                        proof=proof("chat_marker", directed, directed_digest),
                    ),
                    await _bind(
                        holder, _ROLES[3], proof=proof("chat_marker", None, directed_digest)
                    ),
                    await _bind(
                        holder,
                        _ROLES[4],
                        legacy_seq=corrupt,
                        proof=proof("finding", corrupt, corrupt_digest),
                    ),
                ]
                ahead = await _bind(
                    holder, _ROLES[5], legacy_seq=corrupt + 9, proof=proof("finding", finding, good)
                )
                below = await _bind(
                    holder, _ROLES[6], legacy_seq=1, proof=proof("finding", finding + 1, good)
                )
                digest_with_cursor = await _bind(
                    holder, _ROLES[7], legacy_seq=finding, proof=proof("finding", None, good)
                )
                malformed = [
                    await _bind(holder, _ROLES[8], proof=proof("chat", None, good)),
                    await _bind(
                        holder, _ROLES[9], proof={"kind": ["finding"], "payload_sha256": good}
                    ),
                    await _bind(holder, _ROLES[10], proof=proof("finding", None, "A" * 64)),
                    await _bind(holder, _ROLES[11], proof="finding"),
                ]
            finally:
                await close_agents(holder)
    finally:
        journal.close()

    assert all(result["code"] == LEGACY_BINDING_UNVERIFIED for result in results), results
    assert all(result["bound"] is False and "after_seq" not in result for result in results)
    assert "not served by this journal" in results[0]["error"]
    assert results[3]["error"] == "the proof digest matches no served row"
    assert ahead["error"] == "feed binding exceeds the retained journal" and "code" not in ahead
    assert below["error"] == "invalid feed binding"
    assert digest_with_cursor["error"] == "a digest-only binding carries no legacy_seq"
    assert [result["error"] for result in malformed] == ["invalid feed binding"] * 4


async def test_a_digest_alone_binds_only_a_unique_served_row(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "hub.db")
    twin = {"text": "seen twice", "note": "ä"}
    record_finding(journal, twin)
    # a pair inside the first full scan page stops the search there
    record_finding(journal, {"text": "seen twice early"})
    record_finding(journal, {"text": "seen twice early"})
    journal.append_batch(
        [(EventKind.FINDING, {"text": f"filler {index}"}) for index in range(FEED_SCAN_LIMIT)]
    )
    unique = _finding(journal, "seen once")
    record_finding(journal, twin)  # the second copy lies beyond the first scan page
    journal.append(
        EventKind.CHAT,
        {"type": "chat", "sender": "R/pub", "target": "all", "payload": "Decision: keep it"},
    )
    marked = journal.max_seq()
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            holder = await _holder(uri)
            try:
                once = await _bind(
                    holder,
                    _ROLES[0],
                    proof={"kind": "finding", "payload_sha256": _digest(journal, unique)},
                )
                twice = await _bind(
                    holder,
                    _ROLES[1],
                    proof={"kind": "finding", "payload_sha256": _digest(journal, 1)},
                )
                early = await _bind(
                    holder,
                    _ROLES[3],
                    proof={"kind": "finding", "payload_sha256": _digest(journal, 2)},
                )
                marker = await _bind(
                    holder,
                    _ROLES[2],
                    proof={"kind": "chat_marker", "payload_sha256": _digest(journal, marked)},
                )
            finally:
                await close_agents(holder)
    finally:
        journal.close()

    assert (once["bound"], once["after_seq"]) == (True, unique)
    assert twice["code"] == LEGACY_BINDING_UNVERIFIED
    assert twice["error"] == "the proof digest matches more than one served row"
    assert (marker["bound"], marker["after_seq"]) == (True, marked)
    assert early["error"] == "the proof digest matches more than one served row"


async def test_binding_is_rate_limited_and_refused_once_the_consumer_has_an_ack(
    tmp_path: Path,
) -> None:
    journal = EventStore(tmp_path / "hub.db")
    finding = _finding(journal, "a finding")
    proof = {"kind": "finding", "seq": finding, "payload_sha256": "0" * 64}
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            holder = await _holder(uri)
            try:
                first = await _bind(holder, _ROLES[0], legacy_seq=finding, proof=proof)
                again = await _bind(holder, _ROLES[0], legacy_seq=finding, proof=proof)
                page = await _page(holder, _ROLES[1])
                acked = await _reply(
                    holder,
                    "feed_ack",
                    "feed_ack_result",
                    consumer=_ROLES[1],
                    hub_id=page["hub_id"],
                    journal_instance=page["journal_instance"],
                    acked_seq=page["next_after"],
                )
                after_ack = await _bind(holder, _ROLES[1], legacy_seq=finding, proof=proof)
            finally:
                await close_agents(holder)
    finally:
        journal.close()

    assert first["code"] == LEGACY_BINDING_UNVERIFIED
    assert (again["code"], again["error"]) == (
        "rate_limited",
        "one feed binding attempt per consumer per minute",
    )
    assert acked["feed_ack"]["recorded"] is True
    assert after_ack["code"] == "already_bound"


async def test_the_bind_grant_is_the_proof_kind_and_the_consumer_owns_the_connection(
    tmp_path: Path,
) -> None:
    journal = EventStore(tmp_path / "hub.db")
    finding = _finding(journal, "a finding")
    good = _digest(journal, finding)
    policy = AclPolicy([AclRule(FEED_READ, "feed", "finding", namespace="R")])
    hub = SynapseHub(journal=journal, hub_id=_HUB, acl_policy=policy, require_acl=True)
    try:
        async with running_hub(hub) as (_, uri):
            consumer = await connect_agent("R/consumer", uri)
            try:
                granted = await _bind(
                    consumer, "R/consumer", proof={"kind": "finding", "payload_sha256": good}
                )
                denials = []
                for proof in ({"kind": "chat_marker", "payload_sha256": good}, "not a proof"):
                    consumer.recorder.messages.clear()
                    await consumer.agent.send_message(
                        "feed_bind", target="System", consumer="R/consumer", proof=proof
                    )
                    error = await consumer.recorder.wait_for(
                        lambda m: m.get("type") == "error", timeout=_WAIT
                    )
                    denials.append(error["payload"])
                foreign = await _bind(
                    consumer, "R/someone-else", proof={"kind": "finding", "payload_sha256": good}
                )
                other_hub = await _bind(
                    consumer,
                    "R/consumer",
                    hub_id="another-hub",
                    proof={"kind": "finding", "payload_sha256": good},
                )
            finally:
                await close_agents(consumer)
    finally:
        journal.close()

    assert (granted["bound"], granted["after_seq"]) == (True, finding)
    assert denials == [
        "access denied: feed-read on feed:chat_marker",
        "access denied: feed-read on feed:",
    ]
    assert foreign["error"] == "feed consumer is not this connection or one of its roles"
    assert other_hub["error"] == "feed binding names a different hub"


async def test_a_hub_without_a_configured_id_or_an_instance_binds_nothing(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    journal = EventStore(db)
    finding = _finding(journal, "a finding")
    proof = {"kind": "finding", "payload_sha256": _digest(journal, finding)}
    try:
        async with running_hub(SynapseHub(journal=journal)) as (_, uri):
            consumer = await connect_agent("R/consumer", uri)
            try:
                unconfigured = await _bind(consumer, "R/consumer", proof=proof)
            finally:
                await close_agents(consumer)
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            consumer = await connect_agent("R/consumer", uri)
            try:
                with closing(sqlite3.connect(db)) as connection:
                    connection.execute("UPDATE store_format SET journal_instance = NULL")
                    connection.commit()
                no_instance = await _bind(consumer, "R/consumer", proof=proof)
            finally:
                await close_agents(consumer)
    finally:
        journal.close()

    assert unconfigured["error"] == "the feed requires a durable journal and a configured hub id"
    assert no_instance["error"] == "the journal records no instance; a feed cursor cannot be bound"
    assert no_instance["journal_instance"] == ""


async def test_the_cli_and_the_client_bind_or_report_why_not(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "hub.db")
    finding = _finding(journal, "a finding")
    digest = _digest(journal, finding)
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id=_HUB)) as (_, uri):
            connection = ("--uri", uri, "--name", "R/cli")
            bound = await asyncio.to_thread(
                run_cli,
                "feed",
                "bind",
                *connection,
                "--consumer",
                "R/cli",
                "--kind",
                "finding",
                "--digest",
                digest,
                "--seq",
                str(finding),
                "--legacy-seq",
                str(finding),
                "--hub-id",
                _HUB,
            )
            refused = await asyncio.to_thread(
                run_cli,
                "feed",
                "bind",
                "--uri",
                uri,
                "--name",
                "R/cli-2",
                "--consumer",
                "R/cli-2",
                "--kind",
                "finding",
                "--digest",
                "0" * 64,
            )
            unpaired = await asyncio.to_thread(
                run_cli,
                "feed",
                "bind",
                *connection,
                "--consumer",
                "R/cli",
                "--kind",
                "finding",
                "--digest",
                digest,
                "--seq",
                str(finding),
            )
            client = await send_feed_bind(
                uri=uri,
                identity="R/client",
                token=None,
                consumer="R/client",
                kind="finding",
                payload_sha256=digest,
            )
    finally:
        journal.close()
    dead = f"ws://127.0.0.1:{free_port()}"
    unreachable = await asyncio.to_thread(
        run_cli,
        "feed",
        "bind",
        "--uri",
        dead,
        "--name",
        "R/cli",
        "--consumer",
        "R/cli",
        "--kind",
        "finding",
        "--digest",
        digest,
        "--timeout",
        "0.5",
    )
    with pytest.raises(FeedUnavailable, match="did not accept R/client"):
        await send_feed_bind(
            uri=dead,
            identity="R/client",
            token=None,
            consumer="R/client",
            kind="finding",
            payload_sha256=digest,
            seq=1,
            legacy_seq=1,
            timeout=0.5,
        )

    assert bound.returncode == 0, bound.stderr
    assert json.loads(bound.stdout)["after_seq"] == finding
    assert (
        refused.returncode == 1 and json.loads(refused.stdout)["code"] == LEGACY_BINDING_UNVERIFIED
    )
    assert unpaired.returncode == 2 and "--seq and --legacy-seq go together" in unpaired.stderr
    assert (client["bound"], client["after_seq"]) == (True, finding)
    assert unreachable.returncode == 2 and unreachable.stderr.startswith("feed bind: the hub at ")
