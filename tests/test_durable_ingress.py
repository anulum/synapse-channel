# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unit and real-surface tests for durable ingress quotas
"""Bounded per-principal durable chat ingress (events + serialized frame bytes)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import collect_available, read_until_type, running_hub, send_json
from synapse_channel.core.durable_ingress import (
    REASON_BYTES,
    REASON_EVENTS,
    REASON_OVERSIZED,
    REASON_PRINCIPAL_CAPACITY,
    DurableIngressQuota,
    chat_frame_bytes,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def test_chat_frame_bytes_counts_complete_utf8_frame() -> None:
    frame = {"type": "chat", "payload": "café", "extension": {"k": "v"}}
    assert chat_frame_bytes(frame) == len(
        json.dumps(frame, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    assert chat_frame_bytes({"payload": "", "padding": "x" * 16}) > 16
    assert chat_frame_bytes({}) == 2


def test_event_quota_refuses_after_window_fills() -> None:
    quota = DurableIngressQuota(max_events=2, max_bytes=10_000, window_seconds=60.0)
    assert quota.allow("auth-token:a", nbytes=1, now=0.0) == ""
    assert quota.allow("auth-token:a", nbytes=1, now=0.1) == ""
    assert quota.allow("auth-token:a", nbytes=1, now=0.2) == REASON_EVENTS
    # Independent principal keeps its own budget.
    assert quota.allow("auth-token:b", nbytes=1, now=0.2) == ""


def test_byte_quota_and_oversized_single_frame() -> None:
    quota = DurableIngressQuota(max_events=10, max_bytes=10, window_seconds=60.0)
    assert quota.allow("p", nbytes=11, now=0.0) == REASON_OVERSIZED
    assert quota.allow("p", nbytes=6, now=0.0) == ""
    assert quota.allow("p", nbytes=5, now=0.1) == REASON_BYTES
    assert quota.allow("p", nbytes=4, now=0.1) == ""


def test_sliding_window_expires_old_admissions() -> None:
    quota = DurableIngressQuota(max_events=1, max_bytes=100, window_seconds=1.0)
    assert quota.allow("p", nbytes=1, now=0.0) == ""
    assert quota.allow("p", nbytes=1, now=0.5) == REASON_EVENTS
    assert quota.allow("p", nbytes=1, now=1.0) == ""


def test_default_clock_is_monotonic(monkeypatch: pytest.MonkeyPatch) -> None:
    quota = DurableIngressQuota(max_events=1, max_bytes=100, window_seconds=1.0)
    monotonic_ticks = iter([10.0, 11.1])
    monkeypatch.setattr(time, "monotonic", lambda: next(monotonic_ticks))
    monkeypatch.setattr(time, "time", lambda: -1_000_000.0)

    assert quota.allow("p", nbytes=1) == ""
    assert quota.allow("p", nbytes=1) == ""


def test_principal_map_refuses_churn_while_all_buckets_are_active() -> None:
    quota = DurableIngressQuota(max_events=5, max_bytes=100, window_seconds=60.0, max_principals=2)
    assert quota.allow("a", nbytes=1, now=0.0) == ""
    assert quota.allow("b", nbytes=1, now=0.1) == ""
    assert quota.allow("c", nbytes=1, now=0.2) == REASON_PRINCIPAL_CAPACITY
    assert quota.usage("a", now=0.3) == (1, 1)
    assert quota.usage("b", now=0.3) == (1, 1)
    assert quota.usage("c", now=0.3) == (0, 0)


def test_principal_map_reuses_only_an_expired_bucket() -> None:
    quota = DurableIngressQuota(max_events=5, max_bytes=100, window_seconds=1.0, max_principals=2)
    assert quota.allow("a", nbytes=7, now=0.0) == ""
    assert quota.allow("b", nbytes=11, now=0.5) == ""
    assert quota.allow("c", nbytes=13, now=1.1) == ""
    assert quota.usage("a", now=1.1) == (0, 0)
    assert quota.usage("b", now=1.1) == (1, 11)
    assert quota.usage("c", now=1.1) == (1, 13)


def test_running_byte_total_tracks_pruning_without_resumming() -> None:
    quota = DurableIngressQuota(max_events=5, max_bytes=10, window_seconds=1.0)
    assert quota.allow("p", nbytes=6, now=0.0) == ""
    assert quota.allow("p", nbytes=4, now=0.5) == ""
    assert quota.usage("p", now=0.5) == (2, 10)
    assert quota.allow("p", nbytes=6, now=1.1) == ""
    assert quota.usage("p", now=1.1) == (2, 10)


@pytest.mark.asyncio
async def test_hub_refuses_over_event_quota_without_journal_growth(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    quota = DurableIngressQuota(max_events=1, max_bytes=10_000, window_seconds=60.0)
    hub = SynapseHub(hub_id="syn-test", journal=store, durable_ingress_quota=quota)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await send_json(ws, sender="A", type="chat", payload="one")
            first = await read_until_type(ws, "chat")
            assert first["payload"] == "one"
            await send_json(ws, sender="A", type="chat", payload="two")
            error = await read_until_type(ws, "error")
            assert "Durable ingress quota exceeded (events)" in error["payload"]
            leftover = await collect_available(ws, duration=0.05)
    assert all(m.get("type") != "chat" or m.get("payload") != "two" for m in leftover)
    chats = [event for event in store.read_all() if event.kind == EventKind.CHAT]
    store.close()
    assert len(chats) == 1
    assert chats[0].payload["payload"] == "one"
    assert hub.counters.durable_ingress_refused == 1
    assert [m["payload"] for m in hub.chat_history] == ["one"]


@pytest.mark.asyncio
async def test_hub_refuses_over_byte_quota_content_safe() -> None:
    # Content-safe: tiny payloads and a tiny byte cap — never multi-megabyte frames.
    quota = DurableIngressQuota(max_events=50, max_bytes=300, window_seconds=60.0)
    hub = SynapseHub(hub_id="syn-test", durable_ingress_quota=quota)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await send_json(ws, sender="A", type="chat", payload="a")
            await read_until_type(ws, "chat")
            await send_json(ws, sender="A", type="chat", payload="b")
            await read_until_type(ws, "chat")
            await send_json(ws, sender="A", type="chat", payload="x" * 160)
            error = await read_until_type(ws, "error")
    assert "Durable ingress quota exceeded (bytes)" in error["payload"]
    assert hub.counters.durable_ingress_refused == 1
    assert len(hub.chat_history) == 2


@pytest.mark.asyncio
async def test_hub_charges_unknown_fields_before_journal_growth(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    quota = DurableIngressQuota(max_events=10, max_bytes=256, window_seconds=60.0)
    hub = SynapseHub(hub_id="syn-test", journal=store, durable_ingress_quota=quota)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await send_json(ws, sender="A", type="chat", payload="ok")
            await read_until_type(ws, "chat")
            await send_json(ws, sender="A", type="chat", payload="", padding="x" * 300)
            error = await read_until_type(ws, "error")
    chats = [event for event in store.read_all() if event.kind == EventKind.CHAT]
    store.close()
    assert "Durable ingress quota exceeded (oversized)" in error["payload"]
    assert len(chats) == 1
    assert "padding" not in chats[0].payload


@pytest.mark.asyncio
async def test_open_host_principal_shared_across_names() -> None:
    """Two open-hub agents from one host share the open-host principal bucket."""
    quota = DurableIngressQuota(max_events=1, max_bytes=10_000, window_seconds=60.0)
    hub = SynapseHub(hub_id="syn-test", durable_ingress_quota=quota)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as alice, connect(uri) as bob:
            await read_until_type(alice, "welcome")
            await read_until_type(bob, "welcome")
            await send_json(alice, sender="ALICE", type="chat", payload="first")
            await read_until_type(alice, "chat")
            # Bob is a different asserted name but same open-host principal.
            await send_json(bob, sender="BOB", type="chat", payload="second")
            error = await read_until_type(bob, "error")
    assert "Durable ingress quota exceeded" in error["payload"]
    assert len(hub.chat_history) == 1


@pytest.mark.asyncio
async def test_quota_disabled_admits_freely() -> None:
    hub = SynapseHub(hub_id="syn-test", durable_ingress_quota=None)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            for i in range(5):
                await send_json(ws, sender="A", type="chat", payload=str(i))
                await read_until_type(ws, "chat")
    assert hub.counters.durable_ingress_refused == 0
    assert len(hub.chat_history) == 5
