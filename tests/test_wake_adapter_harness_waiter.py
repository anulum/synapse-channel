# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the harness-waiter wake adapter against a real hub and a real waiter
"""The harness-waiter adapter against a hub, a waiter process and a sender of the test's own.

The hub runs in the test with an event store in the test's directory. The
waiter is the real ``synapse wait`` command in a process of its own, and the
sender is the real ``synapse send`` command. "The seat was woken" is the
waiter process ending with status 0; the adapter is then asked about the
message through the same event store, read from a second connection.

Every process gets a home and configuration directory inside the test's
directory and no hub token, so nothing of the workstation's own hub is read
or written.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import sys
from pathlib import Path

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel import wake_marks
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.wake_capability import (
    WAKE_DIRECT,
    WAKE_PANE_BRIDGE,
    WAKE_PASSIVE,
    WAKE_UNKNOWN,
)
from synapse_channel.wake_adapters import ACCEPTED, REFUSED, UNCERTAIN, WakeOutcome, WakeTarget
from synapse_channel.wake_adapters.harness_waiter import (
    ACK_PROVES,
    DELIVERED_AT_ONCE,
    DELIVERED_LATER,
    EXPIRED,
    KIND,
    PENDING,
    PROVES,
    HarnessWaiterAdapter,
    ReceiptView,
    journal_receipts,
)
from synapse_channel.wake_relay import WAKE_FAILED, relay

SEAT = "E2EWAKE/seat-cccc"
OTHER = "E2EWAKE/seat-dddd"
SENDER = "E2EWAKE/sender-eeee"
TARGET = WakeTarget(SEAT)
PROMPT = "fixed wake prompt of the relay"


def _environment(tmp_path: Path) -> dict[str, str]:
    """Return an environment whose home and configuration are the test's own."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    kept = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "LANG", "COVERAGE_PROCESS_CONFIG")
        if key in os.environ
    }
    return {
        **kept,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_STATE_HOME": str(home / "state"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


async def _command(tmp_path: Path, *arguments: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-B",
        "-m",
        "synapse_channel.cli",
        *arguments,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_environment(tmp_path),
        cwd=tmp_path,
    )


async def _armed_waiter(
    tmp_path: Path, hub: SynapseHub, uri: str, seat: str
) -> asyncio.subprocess.Process:
    """Start the real waiter for ``seat`` and return once the hub knows it."""
    waiter = await _command(
        tmp_path,
        "wait",
        "--uri",
        uri,
        "--name",
        f"{seat}-rx",
        "--for",
        seat,
        "--directed-only",
        "--timeout",
        "30",
    )
    for _ in range(400):
        if hub.wake_capability_of(f"{seat}-rx") == WAKE_PASSIVE:
            return waiter
        assert waiter.returncode is None, "the waiter ended before it registered"
        await asyncio.sleep(0.025)
    waiter.kill()
    await waiter.wait()
    raise AssertionError("the waiter did not register with the hub")


async def _declared(hub: SynapseHub, name: str, capability: str) -> None:
    """Return once the hub has recorded the receiver kind ``name`` declared.

    A client declares its kind in the first frame after the hub's welcome, so
    being connected does not yet mean the hub knows it. The hub's own record
    is read until it holds the declared kind.
    """
    for _ in range(400):
        if hub.wake_capability_of(name) == capability:
            return
        await asyncio.sleep(0.025)
    raise AssertionError(f"the hub never recorded {capability!r} for {name}")


async def _send(tmp_path: Path, uri: str, target: str, text: str) -> int:
    """Send one directed message with the real command; return its exit status."""
    sender = await _command(
        tmp_path, "send", "--uri", uri, "--name", f"{SENDER}-tx", "--target", target, text
    )
    await asyncio.wait_for(sender.communicate(), timeout=30)
    assert sender.returncode is not None
    return sender.returncode


def _message_seq(db: Path, text: str) -> int:
    """Return the journal sequence number of the chat message with that text."""
    store = EventStore(db)
    try:
        found = [
            event.seq
            for event in store.iter_events(kinds=(EventKind.CHAT,))
            if event.payload.get("payload") == text
        ]
    finally:
        store.close()
    assert len(found) == 1, found
    return found[0]


async def _ask(adapter: HarnessWaiterAdapter, wake_id: int, seconds: float = 5.0) -> WakeOutcome:
    return await asyncio.to_thread(adapter.wake, TARGET, wake_id, PROMPT, seconds)


async def test_a_message_that_ended_the_seats_waiter_is_accepted(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    adapter = HarnessWaiterAdapter(journal_receipts(db))
    async with running_hub(SynapseHub(hub_id="waiter", journal=EventStore(db))) as (hub, uri):
        waiter = await _armed_waiter(tmp_path, hub, uri, SEAT)
        assert hub.wake_capability_of(f"{SEAT}-rx") == WAKE_PASSIVE
        await _send(tmp_path, uri, SEAT, "wake the waiter")
        assert await asyncio.wait_for(waiter.wait(), timeout=30) == 0
        outcome = await _ask(adapter, _message_seq(db, "wake the waiter"))

    assert (outcome.state, outcome.proves) == (ACCEPTED, PROVES)
    assert adapter.kind == KIND
    assert adapter.available(TARGET).available


async def test_a_message_for_another_seat_is_refused_and_leaves_the_waiter_armed(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    adapter = HarnessWaiterAdapter(journal_receipts(db))
    async with running_hub(SynapseHub(hub_id="waiter", journal=EventStore(db))) as (hub, uri):
        waiter = await _armed_waiter(tmp_path, hub, uri, SEAT)
        other = await _armed_waiter(tmp_path, hub, uri, OTHER)
        await _send(tmp_path, uri, OTHER, "for the other seat")
        assert await asyncio.wait_for(other.wait(), timeout=30) == 0
        outcome = await _ask(adapter, _message_seq(db, "for the other seat"))
        still_armed = waiter.returncode is None
        waiter.terminate()
        await waiter.wait()

    assert still_armed
    assert outcome.state == REFUSED
    assert "does not list the seat" in outcome.detail


async def test_a_message_nobody_took_is_uncertain_before_and_after_a_plain_later_waiter(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    reader = journal_receipts(db)
    adapter = HarnessWaiterAdapter(reader)
    async with running_hub(SynapseHub(hub_id="waiter", journal=EventStore(db))) as (_, uri):
        await _send(tmp_path, uri, SEAT, "nobody is listening")
        wake_id = _message_seq(db, "nobody is listening")
        before = await _ask(adapter, wake_id)
        late = await _command(
            tmp_path,
            "wait",
            "--uri",
            uri,
            "--name",
            f"{SEAT}-rx",
            "--for",
            SEAT,
            "--directed-only",
            "--timeout",
            "3",
        )
        ended = await asyncio.wait_for(late.wait(), timeout=30)
        after = await _ask(adapter, wake_id)
        view = reader(wake_id)

    # A plain waiter is not handed a held message; one that reads the mailbox
    # can be, which is why the state is not a refusal.
    assert ended == 2
    assert view is not None and view.state == PENDING
    assert (before.state, after.state) == (UNCERTAIN, UNCERTAIN)
    assert (before.proves, after.proves) == ("", "")
    assert "waits in the mailbox" in before.detail


async def test_a_mailbox_reading_waiter_that_connects_later_takes_the_message(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    reader = journal_receipts(db)
    adapter = HarnessWaiterAdapter(reader)
    async with running_hub(SynapseHub(hub_id="waiter", journal=EventStore(db))) as (_, uri):
        await _send(tmp_path, uri, SEAT, "sent before the waiter")
        wake_id = _message_seq(db, "sent before the waiter")
        before = await _ask(adapter, wake_id)
        late = await _command(
            tmp_path,
            "arm",
            "--uri",
            uri,
            "--name",
            f"{SEAT}-rx",
            "--for",
            SEAT,
            "--directed-only",
            "--mailbox",
            "--max-wakes",
            "1",
        )
        ended = await asyncio.wait_for(late.wait(), timeout=30)
        after = await _ask(adapter, wake_id)
        view = reader(wake_id)

    # The wake happened after the relay would have been asked: a refusal
    # before it would have let a second adapter wake the seat as well.
    assert ended == 0
    assert view is not None and view.state == DELIVERED_LATER
    assert (before.state, after.state) == (UNCERTAIN, ACCEPTED)
    assert after.proves == ACK_PROVES
    assert view.receipt["ack_receiver"] == f"{SEAT}-rx"
    assert view.receipt["recipient_wake_capabilities"] == {SEAT: WAKE_PASSIVE}


@pytest.mark.parametrize(
    ("declared", "state", "words"),
    [
        (WAKE_DIRECT, REFUSED, "another kind"),
        (WAKE_PANE_BRIDGE, REFUSED, "another kind"),
    ],
)
async def test_a_seat_that_received_the_message_itself_is_not_a_waiter_wake(
    tmp_path: Path, declared: str, state: str, words: str
) -> None:
    db = tmp_path / "hub.db"
    adapter = HarnessWaiterAdapter(journal_receipts(db))
    async with running_hub(SynapseHub(hub_id="waiter", journal=EventStore(db))) as (hub, uri):
        seat = await connect_agent(SEAT, uri, wake_capability=declared)
        try:
            await _declared(hub, SEAT, declared)
            await _send(tmp_path, uri, SEAT, "straight to the seat")
            outcome = await _ask(adapter, _message_seq(db, "straight to the seat"))
        finally:
            await close_agents(seat)

    assert outcome.state == state
    assert words in outcome.detail


async def test_a_sequence_number_without_a_receipt_is_uncertain_after_the_time_limit(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    adapter = HarnessWaiterAdapter(journal_receipts(db))
    async with running_hub(SynapseHub(hub_id="waiter", journal=EventStore(db))) as (hub, uri):
        waiter = await _armed_waiter(tmp_path, hub, uri, SEAT)
        await _send(tmp_path, uri, SEAT, "the only message")
        assert await asyncio.wait_for(waiter.wait(), timeout=30) == 0
        beyond = _message_seq(db, "the only message") + 1000
        started = asyncio.get_running_loop().time()
        outcome = await _ask(adapter, beyond, 0.3)
        elapsed = asyncio.get_running_loop().time() - started

    assert (outcome.state, outcome.proves) == (UNCERTAIN, "")
    assert 0.3 <= elapsed < 5.0


async def test_the_relay_records_the_waiter_as_the_wake_path_and_asks_once(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    asked: list[int] = []
    reader = journal_receipts(db)

    def counted(message_seq: int) -> ReceiptView | None:
        asked.append(message_seq)
        return reader(message_seq)

    adapter = HarnessWaiterAdapter(counted)
    marks = tmp_path / "marks"
    async with running_hub(SynapseHub(hub_id="waiter", journal=EventStore(db))) as (hub, uri):
        waiter = await _armed_waiter(tmp_path, hub, uri, SEAT)
        await _send(tmp_path, uri, SEAT, "through the relay")
        assert await asyncio.wait_for(waiter.wait(), timeout=30) == 0
        wake_id = _message_seq(db, "through the relay")
        first = await asyncio.to_thread(
            relay, marks, TARGET, wake_id, [adapter], deadline_seconds=5.0
        )
        again = await asyncio.to_thread(
            relay, marks, TARGET, wake_id, [adapter], deadline_seconds=5.0
        )

    assert (first.state, first.wake_path, first.proves, first.sent_now) == (
        ACCEPTED,
        KIND,
        PROVES,
        True,
    )
    assert (again.state, again.wake_path, again.sent_now) == (ACCEPTED, KIND, False)
    assert asked == [wake_id]
    (mark,) = wake_marks.read(marks, SEAT, wake_id)
    assert (mark.adapter, mark.state, mark.proves) == (KIND, ACCEPTED, PROVES)


async def test_the_relay_fails_the_wake_when_another_kind_of_receiver_took_the_message(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    adapter = HarnessWaiterAdapter(journal_receipts(db))
    async with running_hub(SynapseHub(hub_id="waiter", journal=EventStore(db))) as (hub, uri):
        seat = await connect_agent(SEAT, uri, wake_capability=WAKE_DIRECT)
        try:
            await _declared(hub, SEAT, WAKE_DIRECT)
            await _send(tmp_path, uri, SEAT, "no waiter at all")
            wake_id = _message_seq(db, "no waiter at all")
            result = await asyncio.to_thread(
                relay, tmp_path / "marks", TARGET, wake_id, [adapter], deadline_seconds=5.0
            )
        finally:
            await close_agents(seat)

    assert result.state == WAKE_FAILED
    assert result.wake_path == ""


async def test_the_relay_tries_nothing_else_for_a_message_that_waits_in_the_mailbox(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    adapter = HarnessWaiterAdapter(journal_receipts(db))
    async with running_hub(SynapseHub(hub_id="waiter", journal=EventStore(db))) as (_, uri):
        await _send(tmp_path, uri, SEAT, "held by the hub")
        wake_id = _message_seq(db, "held by the hub")
        result = await asyncio.to_thread(
            relay, tmp_path / "marks", TARGET, wake_id, [adapter], deadline_seconds=5.0
        )

    assert (result.state, result.wake_path, result.proves) == (UNCERTAIN, KIND, "")


async def test_each_message_is_judged_by_its_own_receipt_among_later_ones(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    adapter = HarnessWaiterAdapter(journal_receipts(db))
    async with running_hub(SynapseHub(hub_id="waiter", journal=EventStore(db))) as (hub, uri):
        waiter = await _armed_waiter(tmp_path, hub, uri, SEAT)
        await _send(tmp_path, uri, SEAT, "first, with a waiter")
        assert await asyncio.wait_for(waiter.wait(), timeout=30) == 0
        await _send(tmp_path, uri, SEAT, "second, after the waiter ended")
        first = await _ask(adapter, _message_seq(db, "first, with a waiter"))
        second = await _ask(adapter, _message_seq(db, "second, after the waiter ended"))

    assert first.state == ACCEPTED
    assert second.state == UNCERTAIN


def test_the_adapter_claims_a_write_and_no_adapter_claims_a_harness_notice() -> None:
    from synapse_channel import wake_adapters
    from synapse_channel.wake_adapters import codex_queue, pane_bridge

    said = {PROVES, ACK_PROVES, codex_queue.PROVES, pane_bridge.PROVES}
    assert PROVES == "written_to_passive_receiver"
    assert said < wake_adapters.PROVES
    assert wake_adapters.PROVES - said == {"harness_notice_written"}


def test_a_store_that_does_not_exist_gives_no_receipt_and_stays_absent(tmp_path: Path) -> None:
    absent = tmp_path / "nowhere" / "absent.db"

    assert journal_receipts(absent)(1) is None
    assert not absent.exists() and not absent.parent.exists()


async def test_reading_receipts_leaves_the_stores_files_as_they_were(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    try:
        async with running_hub(SynapseHub(hub_id="waiter", journal=store)) as (hub, uri):
            waiter = await _armed_waiter(tmp_path, hub, uri, SEAT)
            await _send(tmp_path, uri, SEAT, "read me only")
            assert await asyncio.wait_for(waiter.wait(), timeout=30) == 0
            wake_id = _message_seq(db, "read me only")
    finally:
        store.close()

    def state() -> dict[str, tuple[int, int, str]]:
        return {
            path.name: (
                path.stat().st_size,
                path.stat().st_mode,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in sorted(tmp_path.iterdir())
            if path.name.startswith("hub.db")
        }

    before = state()
    outcome = HarnessWaiterAdapter(journal_receipts(db)).wake(TARGET, wake_id, PROMPT, 2.0)
    after = state()

    assert outcome.state == ACCEPTED
    # The store's own file is untouched: same size, same mode, same bytes.
    assert after["hub.db"] == before["hub.db"]
    # A read of a store in write-ahead mode may leave the engine's two empty
    # companion files beside a store that was closed cleanly; nothing else
    # appears, and the write-ahead file holds no data.
    assert set(after) - set(before) <= {"hub.db-shm", "hub.db-wal"}
    assert after.get("hub.db-wal", (0, 0, ""))[0] == 0


def test_an_encrypted_reader_that_cannot_be_set_up_is_uncertain_at_once(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    EventStore(db).close()
    key_file = tmp_path / "not-a-key"
    key_file.write_bytes(b"too short")
    looks: list[float] = []
    adapter = HarnessWaiterAdapter(journal_receipts(db, key_file=key_file), sleeper=looks.append)

    outcome = adapter.wake(TARGET, 1, PROMPT, 30.0)

    assert (outcome.state, outcome.proves) == (UNCERTAIN, "")
    assert "could not be set up" in outcome.detail
    assert looks == []


@pytest.mark.parametrize("wake_id", [0, -3, True])
def test_no_message_number_is_refused_without_reading_anything(wake_id: int) -> None:
    def never(message_seq: int) -> ReceiptView | None:
        raise AssertionError(f"the receipt of {message_seq} was read")

    outcome = HarnessWaiterAdapter(never).wake(TARGET, wake_id, PROMPT, 1.0)

    assert (outcome.state, outcome.detail) == (REFUSED, "not asked: no message is named")


def test_no_seat_is_unavailable_and_refused_without_reading_anything() -> None:
    def never(message_seq: int) -> ReceiptView | None:
        raise AssertionError(f"the receipt of {message_seq} was read")

    adapter = HarnessWaiterAdapter(never)
    nobody = WakeTarget("")

    assert not adapter.available(nobody).available
    assert adapter.wake(nobody, 1, PROMPT, 1.0).state == REFUSED


# The states and shapes below were not produced through a hub here: a receipt
# expires only when the hub's pending window overflows, and the other shapes
# do not occur in receipts the hub writes. They are constructed and are the
# only constructed receipts of this file.
@pytest.mark.parametrize(
    ("view", "state"),
    [
        (ReceiptView(EXPIRED, {"delivered": False, "recipients": []}), UNCERTAIN),
        (ReceiptView(PENDING, {"delivered": False, "recipients": []}), UNCERTAIN),
        (ReceiptView(DELIVERED_AT_ONCE, {"recipients": SEAT}), REFUSED),
        (ReceiptView(DELIVERED_AT_ONCE, {"recipients": [OTHER]}), REFUSED),
        (ReceiptView(DELIVERED_AT_ONCE, {"recipients": [SEAT]}), UNCERTAIN),
        (
            ReceiptView(
                DELIVERED_AT_ONCE,
                {"recipients": [SEAT], "recipient_wake_capabilities": [WAKE_PASSIVE]},
            ),
            UNCERTAIN,
        ),
        (
            ReceiptView(
                DELIVERED_AT_ONCE,
                {"recipients": [SEAT], "recipient_wake_capabilities": {SEAT: 7}},
            ),
            UNCERTAIN,
        ),
        (ReceiptView("requested", {}), UNCERTAIN),
        (ReceiptView("a state this adapter does not know", {}), UNCERTAIN),
    ],
)
def test_constructed_receipt_states_are_never_accepted(view: ReceiptView, state: str) -> None:
    adapter = HarnessWaiterAdapter(lambda _seq: view)
    outcome = adapter.wake(TARGET, 7, PROMPT, 0.1)
    assert (outcome.state, outcome.proves) == (state, "")


def test_bound_adapter_refuses_other_seats_sessions_hosts_and_invalid_deadlines(
    tmp_path: Path,
) -> None:
    """Public binding checks precede any real receipt lookup."""
    target = WakeTarget(SEAT, "01a0fcac-3407-71c3-9081-0bff345ef6c2", "host")
    adapter = HarnessWaiterAdapter(journal_receipts(tmp_path / "absent.db"), expected_target=target)
    for other in (
        WakeTarget(OTHER, target.native_session, "host"),
        WakeTarget(SEAT, "other", "host"),
        WakeTarget(SEAT, target.native_session, "another"),
    ):
        assert not adapter.available(other).available
        assert adapter.wake(other, 1, PROMPT, 1.0).state == REFUSED
    for seconds in (True, -1.0, float("inf"), float("nan")):
        assert adapter.wake(target, 1, PROMPT, seconds).state == REFUSED
    assert not (tmp_path / "absent.db").exists()


async def test_real_deferred_ack_from_a_direct_receiver_is_not_a_passive_wake(
    tmp_path: Path,
) -> None:
    """A recipient's actual ACK is recorded under its own declared receiver kind."""
    db = tmp_path / "hub.db"
    async with running_hub(SynapseHub(hub_id="ack-kind", journal=EventStore(db))) as (hub, uri):
        await _send(tmp_path, uri, SEAT, "direct receiver backlog")
        seq = _message_seq(db, "direct receiver backlog")
        recipient = await connect_agent(SEAT, uri, wake_capability=WAKE_DIRECT)
        try:
            await _declared(hub, SEAT, WAKE_DIRECT)
            await recipient.agent.send_message("ack", target="System", payload="", seq=seq)
            reader = journal_receipts(db)
            for _ in range(200):
                view = reader(seq)
                if view is not None and view.state == DELIVERED_LATER:
                    break
                await asyncio.sleep(0.01)
            assert view is not None and view.state == DELIVERED_LATER
            assert view.receipt["ack_receiver"] == SEAT
            assert view.receipt["recipient_wake_capabilities"] == {SEAT: WAKE_DIRECT}
            result = await _ask(HarnessWaiterAdapter(reader), seq)
            assert (result.state, result.proves) == (UNCERTAIN, "")
        finally:
            await close_agents(recipient)


async def test_an_actual_passive_sidecar_write_cannot_be_refused_because_a_direct_seat_exists(
    tmp_path: Path,
) -> None:
    """A real waiter delivery must suppress fallback even beside a direct receiver."""
    db = tmp_path / "hub.db"
    async with running_hub(SynapseHub(hub_id="mixed-writers", journal=EventStore(db))) as (
        hub,
        uri,
    ):
        recipient = await connect_agent(SEAT, uri, wake_capability=WAKE_DIRECT)
        waiter = await _armed_waiter(tmp_path, hub, uri, SEAT)
        try:
            assert await _send(tmp_path, uri, SEAT, "both receiver kinds") == 0
            assert await asyncio.wait_for(waiter.wait(), timeout=20) == 0
            result = await _ask(
                HarnessWaiterAdapter(journal_receipts(db)), _message_seq(db, "both receiver kinds")
            )
            assert (result.state, result.proves) == (ACCEPTED, PROVES)
        finally:
            if waiter.returncode is None:
                waiter.kill()
                await waiter.wait()
            await close_agents(recipient)


async def test_unknown_primary_and_non_passive_sidecar_do_not_certify_passive_absence(
    tmp_path: Path,
) -> None:
    """Real captured unknown declarations prevent an unsafe active fallback."""
    db = tmp_path / "hub.db"
    async with running_hub(SynapseHub(hub_id="unknown-writer", journal=EventStore(db))) as (
        hub,
        uri,
    ):
        recipient = await connect_agent(SEAT, uri, wake_capability=WAKE_UNKNOWN)
        waiter = await _command(
            tmp_path,
            "wait",
            "--uri",
            uri,
            "--name",
            f"{SEAT}-rx",
            "--for",
            SEAT,
            "--directed-only",
            "--wake-capability",
            WAKE_PANE_BRIDGE,
            "--timeout",
            "20",
        )
        try:
            await _declared(hub, f"{SEAT}-rx", WAKE_PANE_BRIDGE)
            await _send(tmp_path, uri, SEAT, "unknown actual writer")
            assert await asyncio.wait_for(waiter.wait(), timeout=25) == 0
            result = await _ask(
                HarnessWaiterAdapter(journal_receipts(db)),
                _message_seq(db, "unknown actual writer"),
            )
            assert (result.state, result.proves) == (UNCERTAIN, "")
        finally:
            if waiter.returncode is None:
                waiter.kill()
                await waiter.wait()
            await close_agents(recipient)


async def test_actual_channel_wire_receipt_without_transport_provenance_is_uncertain(
    tmp_path: Path,
) -> None:
    """An actual unqualified hub receipt cannot certify a passive transport write."""
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(hub_id="unqualified-wire", journal=store)) as (_, uri):
        owner = await connect_agent(SENDER, uri)
        recipient = await connect_agent(SEAT, uri, wake_capability=WAKE_PASSIVE)
        try:
            await owner.agent.send_message("channel_create", channel="proof-channel")
            await owner.recorder.wait_for(
                lambda m: m.get("type") == "channel_result" and m.get("ok") is True
            )
            await owner.agent.send_message("channel_invite", channel="proof-channel", invitee=SEAT)
            await recipient.agent.send_message("channel_join", channel="proof-channel")
            await recipient.recorder.wait_for(
                lambda m: m.get("type") == "channel_result" and m.get("ok") is True
            )
            await owner.agent.send_message(
                "chat",
                channel="proof-channel",
                payload="actual unqualified wire",
                receipt_requested=True,
            )
            frame = await owner.recorder.wait_for(lambda m: m.get("type") == "delivery_receipt")
            assert frame["delivered"] is True and not frame["deferred"]
            seq = next(
                event.seq
                for event in store.iter_events(kinds=(EventKind.CHAT,))
                if event.payload.get("msg_id") == frame["message_id"]
            )
            view = ReceiptView(DELIVERED_AT_ONCE, frame)
            # This reader uses the genuine correlated wire receipt, not a made-up payload.
            result = HarnessWaiterAdapter(lambda number: view if number == seq else None).wake(
                TARGET, seq, PROMPT, 1.0
            )
            assert (result.state, result.proves) == (UNCERTAIN, "")
        finally:
            await close_agents(owner, recipient)


async def test_real_passive_observation_precedes_an_available_active_transport(
    tmp_path: Path,
) -> None:
    """An existing waiter wake prevents an active child even when configured last."""
    from synapse_channel.wake_adapters.codex_queue import CodexQueueAdapter

    db = tmp_path / "hub.db"
    async with running_hub(SynapseHub(hub_id="observe-first", journal=EventStore(db))) as (
        hub,
        uri,
    ):
        waiter = await _armed_waiter(tmp_path, hub, uri, SEAT)
        try:
            await _send(tmp_path, uri, SEAT, "already woke passive receiver")
            assert await asyncio.wait_for(waiter.wait(), timeout=20) == 0
            seq = _message_seq(db, "already woke passive receiver")
            target = WakeTarget(SEAT, "01a0fcac-3407-71c3-9081-0bff345ef6c2", socket.gethostname())
            active = CodexQueueAdapter(Path(sys.executable))
            assert active.available(target).available
            result = await asyncio.to_thread(
                relay,
                tmp_path / "marks",
                target,
                seq,
                [active, HarnessWaiterAdapter(journal_receipts(db))],
            )
            assert (result.state, result.wake_path, result.proves) == (ACCEPTED, KIND, PROVES)
            assert len(result.attempts) == 1
            assert result.attempts[0].adapter == KIND
        finally:
            if waiter.returncode is None:
                waiter.kill()
                await waiter.wait()
