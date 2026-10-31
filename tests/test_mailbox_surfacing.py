# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — mailbox surfacing contract: no silent consumption

"""A directed message is either surfaced to the operator or stays pending.

The 2026-07-10 P0 exposed two silent-consumption paths: a waiter printed
only the LAST frame of a replay burst while its persisted cursor advanced
past the whole backlog, and the mailbox cursor advanced even for frames
the wake filter refused. Both lose messages invisibly — durable in the
feed, gone from the live path. This surface pins the repaired contract
against a real hub with a durable journal: every surfaced frame prints,
the persisted resume point covers exactly the surfaced frames, and a
frame a waiter refuses stays pending for the next (or correctly bound)
waiter's replay.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel import cli_messaging_wait
from synapse_channel.cli_arm import _arm
from synapse_channel.cli_messaging_wait import _wait
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.mailbox_cursor import load_cursor

SEAT = "PROJ/agent-x"


async def _seed_backlog(uri: str, payloads: list[str], target: str = SEAT) -> None:
    """Send directed messages that land in the hub journal as backlog."""
    sender = await connect_agent("peer", uri)
    try:
        for payload in payloads:
            await sender.agent.chat(payload, target=target)
    finally:
        await close_agents(sender)


async def test_a_replay_burst_surfaces_every_message_not_just_the_last(
    tmp_path: Path,
    capsys: Any,
) -> None:
    db = tmp_path / "hub.db"
    cursor = tmp_path / "cursor"
    store = EventStore(db)
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            await _seed_backlog(uri, ["first", "second", "third"])
            code = await _wait(
                uri=uri,
                name=f"{SEAT}-rx",
                for_name=SEAT,
                timeout=5.0,
                directed_only=True,
                mailbox=True,
                mailbox_cursor_path=cursor,
            )
    finally:
        store.close()

    out = capsys.readouterr().out
    assert code == 0
    for payload in ("first", "second", "third"):
        assert payload in out, f"burst frame {payload!r} was swallowed: {out!r}"


async def test_the_persisted_cursor_covers_exactly_the_surfaced_frames(
    tmp_path: Path,
    capsys: Any,
) -> None:
    db = tmp_path / "hub.db"
    cursor = tmp_path / "cursor"
    store = EventStore(db)
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            await _seed_backlog(uri, ["alpha", "beta"])
            first = await _wait(
                uri=uri,
                name=f"{SEAT}-rx",
                for_name=SEAT,
                timeout=5.0,
                directed_only=True,
                mailbox=True,
                mailbox_cursor_path=cursor,
            )
            saved = load_cursor(cursor)
            # A second arm resumes past the surfaced frames: nothing replays,
            # nothing re-wakes — the backlog was delivered exactly once.
            second = await _wait(
                uri=uri,
                name=f"{SEAT}-rx",
                for_name=SEAT,
                timeout=0.5,
                directed_only=True,
                mailbox=True,
                mailbox_cursor_path=cursor,
            )
    finally:
        store.close()

    out = capsys.readouterr().out
    assert first == 0
    assert saved > 0
    assert out.count("alpha") == 1 and out.count("beta") == 1
    assert second == 2  # timeout, nothing pending — and nothing lost


async def test_max_wakes_one_surfaces_the_full_backlog_before_rearm(
    tmp_path: Path,
    capsys: Any,
) -> None:
    """One arm wake drains its collected replay burst without skipping frames."""
    db = tmp_path / "hub.db"
    cursor = tmp_path / "cursor"
    store = EventStore(db)
    second_arm: asyncio.Task[int] | None = None
    try:
        async with running_hub(SynapseHub(journal=store)) as (hub, uri):
            payloads = ["oldest", "middle", "newest"]
            await _seed_backlog(uri, payloads)
            first = await _arm(
                uri=uri,
                name=f"{SEAT}-rx",
                for_name=SEAT,
                max_wakes=1,
                reconnect_delay=0.0,
                mailbox=True,
                mailbox_cursor_path=cursor,
            )
            second_arm = asyncio.create_task(
                _arm(
                    uri=uri,
                    name=f"{SEAT}-rx",
                    for_name=SEAT,
                    max_wakes=1,
                    reconnect_delay=0.0,
                    mailbox=True,
                    mailbox_cursor_path=cursor,
                )
            )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 2.0
            while (
                not second_arm.done()
                and f"{SEAT}-rx" not in hub.online_agents()
                and loop.time() < deadline
            ):
                await asyncio.sleep(0.01)
            assert f"{SEAT}-rx" in hub.online_agents(), "re-arm did not establish presence"
            await asyncio.sleep(0.2)
            assert not second_arm.done(), "re-arm replayed backlog that the first arm surfaced"
    finally:
        if second_arm is not None:
            second_arm.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await second_arm
        store.close()

    out = capsys.readouterr().out
    assert first == 0
    assert load_cursor(cursor) > 0
    for payload in payloads:
        assert out.count(payload) == 1


async def test_a_refused_frame_stays_pending_for_the_next_waiter(
    tmp_path: Path,
    capsys: Any,
) -> None:
    # A waiter whose filter refuses the frame (here: a waiter for a DIFFERENT
    # seat holding the same cursor file) must not consume it: the correctly
    # bound waiter that arms next still receives it on replay.
    db = tmp_path / "hub.db"
    cursor = tmp_path / "cursor"
    store = EventStore(db)
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            await _seed_backlog(uri, ["payload for the real seat"])
            wrong = await _wait(
                uri=uri,
                name="PROJ/agent-other-rx",
                for_name="PROJ/agent-other",
                timeout=0.5,
                directed_only=True,
                mailbox=True,
                mailbox_cursor_path=cursor,
            )
            right = await _wait(
                uri=uri,
                name=f"{SEAT}-rx",
                for_name=SEAT,
                timeout=5.0,
                directed_only=True,
                mailbox=True,
                mailbox_cursor_path=cursor,
            )
    finally:
        store.close()

    out = capsys.readouterr().out
    assert wrong == 2  # timed out: the frame is not for that seat
    assert right == 0
    assert "payload for the real seat" in out


async def test_a_live_frame_the_filter_drops_does_not_advance_the_cursor(
    tmp_path: Path,
    capsys: Any,
) -> None:
    # Live (non-replayed) traffic through a connected waiter: a broadcast in
    # directed-only mode is seen by the socket but never surfaced — it must
    # not advance the persisted resume point either.
    import asyncio

    db = tmp_path / "hub.db"
    cursor = tmp_path / "cursor"
    store = EventStore(db)
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            wait_task = asyncio.create_task(
                _wait(
                    uri=uri,
                    name=f"{SEAT}-rx",
                    for_name=SEAT,
                    timeout=2.0,
                    directed_only=True,
                    mailbox=True,
                    mailbox_cursor_path=cursor,
                )
            )
            await asyncio.sleep(0.3)  # let the waiter connect
            await _seed_backlog(uri, ["routine broadcast"], target="all")
            code = await wait_task
    finally:
        store.close()

    assert code == 2  # broadcast does not wake a directed-only waiter
    assert load_cursor(cursor) == 0, "an unsurfaced broadcast advanced the cursor"


async def _online(hub: SynapseHub, name: str, waiter: asyncio.Task[int]) -> None:
    """Wait until the real waiter registered, failing if it exits first."""
    deadline = asyncio.get_running_loop().time() + 5
    while name not in hub.online_agents():
        assert not waiter.done(), "the waiter exited before registering"
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.01)


async def test_a_burst_straddling_a_poll_tick_surfaces_in_one_wake(
    tmp_path: Path,
    capsys: Any,
    monkeypatch: Any,
) -> None:
    """A frame arriving inside the quiet window joins the wake of the first frame."""
    monkeypatch.setattr(cli_messaging_wait, "BURST_QUIET_SECONDS", 0.3)
    cursor = tmp_path / "cursor"
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (hub, uri):
            waiter = asyncio.create_task(
                _wait(
                    uri=uri,
                    name=f"{SEAT}-rx",
                    for_name=SEAT,
                    timeout=5.0,
                    directed_only=True,
                    mailbox=True,
                    mailbox_cursor_path=cursor,
                )
            )
            await _online(hub, f"{SEAT}-rx", waiter)
            sender = await connect_agent("peer", uri)
            try:
                await sender.agent.chat("burst-one", target=SEAT)
                await asyncio.sleep(0.15)
                await sender.agent.chat("burst-two", target=SEAT)
                code = await waiter
            finally:
                await close_agents(sender)
            last_seq = max(event.seq for event in store.iter_events(kinds=frozenset({"chat"})))
    finally:
        store.close()

    out = capsys.readouterr().out
    assert code == 0
    assert out.count("burst-one") == 1 and out.count("burst-two") == 1
    assert load_cursor(cursor) == last_seq


async def test_a_continuous_stream_is_surfaced_at_the_burst_limit(
    tmp_path: Path,
    capsys: Any,
    monkeypatch: Any,
) -> None:
    """A stream that never falls quiet still wakes, covering only what it surfaced."""
    monkeypatch.setattr(cli_messaging_wait, "BURST_QUIET_SECONDS", 0.05)
    monkeypatch.setattr(cli_messaging_wait, "BURST_LIMIT_SECONDS", 0.3)
    cursor = tmp_path / "cursor"
    store = EventStore(tmp_path / "hub.db")
    payloads = [f"stream-{index:02d}" for index in range(40)]
    try:
        async with running_hub(SynapseHub(journal=store)) as (hub, uri):
            waiter = asyncio.create_task(
                _wait(
                    uri=uri,
                    name=f"{SEAT}-rx",
                    for_name=SEAT,
                    timeout=5.0,
                    directed_only=True,
                    mailbox=True,
                    mailbox_cursor_path=cursor,
                )
            )
            await _online(hub, f"{SEAT}-rx", waiter)
            sender = await connect_agent("peer", uri)
            try:
                for payload in payloads:
                    await sender.agent.chat(payload, target=SEAT)
                    await asyncio.sleep(0.03)
                code = await waiter
            finally:
                await close_agents(sender)
            sequences = {
                str(event.payload.get("payload")): event.seq
                for event in store.iter_events(kinds=frozenset({"chat"}))
            }
    finally:
        store.close()

    out = capsys.readouterr().out
    surfaced = [payload for payload in payloads if payload in out]
    assert code == 0
    assert surfaced and surfaced == payloads[: len(surfaced)]
    assert payloads[-1] not in surfaced
    assert load_cursor(cursor) == sequences[surfaced[-1]]


async def test_a_closed_connection_ends_the_quiet_window_at_once(
    tmp_path: Path,
    capsys: Any,
    monkeypatch: Any,
) -> None:
    """A burst still in progress when the hub goes away is surfaced, not awaited."""
    monkeypatch.setattr(cli_messaging_wait, "BURST_QUIET_SECONDS", 0.4)
    monkeypatch.setattr(cli_messaging_wait, "BURST_LIMIT_SECONDS", 30.0)
    cursor = tmp_path / "cursor"
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (hub, uri):
            waiter = asyncio.create_task(
                _wait(
                    uri=uri,
                    name=f"{SEAT}-rx",
                    for_name=SEAT,
                    timeout=5.0,
                    directed_only=True,
                    mailbox=True,
                    mailbox_cursor_path=cursor,
                )
            )
            await _online(hub, f"{SEAT}-rx", waiter)
            sender = await connect_agent("peer", uri)
            try:
                await sender.agent.chat("before-close-one", target=SEAT)
                await asyncio.sleep(0.15)
                await sender.agent.chat("before-close-two", target=SEAT)
                await asyncio.sleep(0.05)
            finally:
                await close_agents(sender)
        started = asyncio.get_running_loop().time()
        code = await asyncio.wait_for(waiter, timeout=5)
        elapsed = asyncio.get_running_loop().time() - started
    finally:
        store.close()

    out = capsys.readouterr().out
    assert code == 0
    assert "before-close-one" in out and "before-close-two" in out
    assert elapsed < 5
