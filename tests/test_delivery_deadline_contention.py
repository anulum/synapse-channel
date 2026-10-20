# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deadline expiry during native SQLite writer contention
"""Keep expired deliveries away from recipients after a real delayed commit."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.handlers.delivery_modes import deliver_pending_delivery_notifications
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from test_delivery_hub import _intent, _register


class _WriterContention:
    """Hold an independent real writer until the admitted request's actual deadline."""

    def __init__(self, path: Path) -> None:
        """Prepare observation and writer synchronization without opening a connection."""
        self.path = path
        self.connection: sqlite3.Connection | None = None
        self.armed = False
        self.deadline = 0.0
        self.entered_at = 0.0
        self.released_at = 0.0
        self.locked = threading.Event()
        self.entered = threading.Event()
        self.stop = threading.Event()
        self.errors: list[Exception] = []
        self.thread = threading.Thread(target=self.hold, name="delivery-writer-contention")

    def audit(self, event: str, args: tuple[object, ...]) -> None:
        """Capture the first native handle without calling its unfinished constructor."""
        if event == "sqlite3.connect/handle" and self.connection is None:
            connection = args[0]
            assert isinstance(connection, sqlite3.Connection)
            self.connection = connection

    def observe(self) -> None:
        """Attach the public trace callback after the actual store finishes opening."""
        assert self.connection is not None
        assert Path(self.connection.execute("PRAGMA database_list").fetchone()[2]) == self.path
        self.connection.set_trace_callback(self.trace)

    def trace(self, statement: str) -> None:
        """Observe entry to the real admission transaction before its lock wait."""
        if self.armed and statement == "BEGIN IMMEDIATE" and not self.entered.is_set():
            self.entered_at = time.time()
            self.entered.set()

    def hold(self) -> None:
        """Acquire and release a separate native SQLite transaction on its own thread."""
        try:
            with closing(sqlite3.connect(self.path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self.locked.set()
                try:
                    if not self.entered.wait(4):
                        raise AssertionError("the hub did not attempt its admission transaction")
                    self.stop.wait(max(0, self.deadline + 0.1 - time.time()))
                    self.released_at = time.time()
                finally:
                    connection.rollback()
        except Exception as exc:
            self.errors.append(exc)
            self.locked.set()

    def close(self) -> None:
        """Unblock and join only this fixture's writer, including failed journeys."""
        self.armed = False
        self.stop.set()
        self.entered.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=5)
            assert not self.thread.is_alive(), "the fixture writer did not stop"


async def _journey(path: Path) -> None:
    """Send a real socket intent before its deadline and delay only native DB custody."""
    contention = _WriterContention(path)
    # Audit hooks cannot be removed: this whole journey runs in its own child process.
    sys.addaudithook(contention.audit)
    with EventStore(path) as store:
        contention.observe()
        async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (_, uri):
            receiver, session = await _register(
                uri, "P/receiver", capabilities={"follow_up": "native"}
            )
            sender, _ = await _register(uri, "P/author")
            try:
                assert session is not None
                await sender.send(
                    json.dumps(
                        {"sender": "P/author", "type": MessageType.WHO_REQUEST, "target": "System"}
                    )
                )
                await read_until_type(sender, MessageType.WHO_SNAPSHOT)
                request = _intent(session["incarnation"])
                contention.deadline = request["deadline"] = time.time() + 2
                contention.thread.start()
                assert await asyncio.to_thread(contention.locked.wait, 3)
                assert not contention.errors
                contention.armed = True

                await sender.send(json.dumps(request))

                queued = await read_until_type(sender, MessageType.DELIVERY_STATUS)
                expired = await read_until_type(sender, MessageType.DELIVERY_STATUS)
                assert queued["stage"] == "queued" and not queued["task_completed"]
                assert expired["stage"] == "expired"
                await sender.send(
                    json.dumps(
                        {
                            "sender": "P/author",
                            "type": MessageType.DELIVERY_STATUS_REQUEST,
                            "target": "System",
                            "protocol_version": 3,
                            "operation_key": queued["operation_key"],
                        }
                    )
                )
                confirmed = await read_until_type(sender, MessageType.DELIVERY_STATUS)
                assert confirmed["stage"] == "expired" and not confirmed["task_completed"]
                assert (
                    not confirmed["boundary_delivered"] and not confirmed["explicitly_acknowledged"]
                )
                assert contention.entered_at < contention.deadline <= contention.released_at
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        read_until_type(receiver, MessageType.DELIVERY_OFFER), timeout=0.15
                    )
                record = store.delivery.get(queued["operation_key"])
                assert record is not None and record.stage == "expired"
                assert [event.kind for event in store.iter_events()] == [
                    "delivery_intent_accepted",
                    "delivery_intent_queued",
                    "delivery_intent_transition",
                ]
            finally:
                contention.close()
                await sender.close()
                await receiver.close()
            assert not contention.errors, contention.errors


@pytest.mark.real_hub
def test_elapsed_storage_deadline_never_offers_or_acknowledges_work(tmp_path: Path) -> None:
    """A real SQLite lock crossing the deadline expires work before recipient input."""
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(tmp_path / "hub.db")],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


class _ReplayReadDelay:
    """Delay one actual pending-offer SELECT without changing its SQL or result."""

    def __init__(self) -> None:
        """Keep the genuine connection and native timing observations."""
        self.connection: sqlite3.Connection | None = None
        self.deadline = 0.0
        self.entered_at = 0.0
        self.returned_at = 0.0

    def audit(self, event: str, args: tuple[object, ...]) -> None:
        """Capture the first store handle in this isolated child process."""
        if event == "sqlite3.connect/handle" and self.connection is None:
            connection = args[0]
            assert isinstance(connection, sqlite3.Connection)
            self.connection = connection

    def trace(self, statement: str) -> None:
        """Hold the native read until its real deadline passes, once only."""
        if (
            not self.entered_at
            and "FROM delivery_requests WHERE target = " in statement
            and "AND stage = 'queued'" in statement
        ):
            self.entered_at = time.time()
            threading.Event().wait(max(0, self.deadline + 0.1 - time.time()))
            self.returned_at = time.time()


async def _replay_journey(path: Path) -> None:
    """Cross a real deadline during native lookup after the initial expiry sweep."""
    delay = _ReplayReadDelay()
    sys.addaudithook(delay.audit)
    with EventStore(path) as store:
        async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (hub, uri):
            receiver, session = await _register(
                uri, "P/receiver", capabilities={"follow_up": "native"}
            )
            sender, _ = await _register(uri, "P/author")
            try:
                assert session is not None and delay.connection is not None
                request = _intent(session["incarnation"])
                delay.deadline = request["deadline"] = time.time() + 4
                await sender.send(json.dumps(request))
                queued = await read_until_type(sender, MessageType.DELIVERY_STATUS)
                await read_until_type(receiver, MessageType.DELIVERY_OFFER)
                assert queued["stage"] == "queued"
                assert Path(delay.connection.execute("PRAGMA database_list").fetchone()[2]) == path
                delay.connection.set_trace_callback(delay.trace)

                await deliver_pending_delivery_notifications(
                    hub, sender="P/receiver", websocket=hub.clients.agent_sockets["P/receiver"]
                )

                assert delay.entered_at < delay.deadline <= delay.returned_at
                expired = await read_until_type(sender, MessageType.DELIVERY_STATUS)
                assert expired["operation_key"] == queued["operation_key"]
                assert expired["stage"] == "expired"
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(receiver.recv(), timeout=0.15)
                record = store.delivery.get(queued["operation_key"])
                assert record is not None and record.stage == "expired"
                assert not record.boundary_delivered and not record.explicitly_acknowledged
                assert [event.kind for event in store.iter_events()] == [
                    "delivery_intent_accepted",
                    "delivery_intent_queued",
                    "delivery_intent_transition",
                ]
            finally:
                if delay.connection is not None:
                    delay.connection.set_trace_callback(None)
                await sender.close()
                await receiver.close()


@pytest.mark.real_hub
def test_deadline_crossed_during_native_replay_read_prevents_another_offer(tmp_path: Path) -> None:
    """A real delayed SELECT cannot publish an offer after its deadline."""
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(tmp_path / "hub.db"), "replay"],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    asyncio.run((_replay_journey if len(sys.argv) == 3 else _journey)(Path(sys.argv[1])))
