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

from pathlib import Path
from typing import Any

from hub_e2e_helpers import close_agents, connect_agent, running_hub
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
