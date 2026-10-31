# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real operator request lifetime tests
"""Cancel and drain real pending operator socket commands before store close."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import struct
import time
from contextlib import AsyncExitStack
from dataclasses import replace
from pathlib import Path

import pytest

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import UnsupportedProtectedWriteHistoryError
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_enrollment_control import ProtectedEnrollmentControl
from synapse_channel.core.protected_write_enrollment_journal import (
    ISSUE_EVENT_KIND,
    REVOKE_EVENT_KIND,
    enrollment_issue_payload,
)
from synapse_channel.core.protected_write_operator_connections import retain_operator_connections
from test_protected_write_enrollment_journal import _enrollment


@pytest.fixture(autouse=True)
def _socket_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use real relative socket names beneath a deliberately long private directory."""
    directory = tmp_path / ("socket-owner-" + "x" * 108)
    directory.mkdir()
    monkeypatch.chdir(directory)


@pytest.mark.parametrize("interruption", ["peer_eof", "owner_cancel", "owner_repeated_cancel"])
@pytest.mark.parametrize("operation", ["issue", "revoke"])
@pytest.mark.asyncio
async def test_interrupted_operator_durable_append_preserves_registry_and_replay(
    tmp_path: Path, interruption: str, operation: str
) -> None:
    """Real SQLite contention delays commit beyond peer EOF or owner cancellation.

    The shared-UID component listener tests actor/transport durability only;
    dedicated deployment credentials and the root-owned endpoint remain separate
    privileged acceptance requirements. No physical writer permission is granted.
    """
    path = tmp_path / "contended.db"
    enrollment = _enrollment()
    same_key = replace(enrollment, session_id="replacement-session")
    same_secret = replace(
        enrollment,
        session_id="replacement-secret",
        key=replace(enrollment.key, key_id="replacement-key"),
    )
    keys = {grant.session_id: grant.key for grant in (enrollment, same_key, same_secret)}
    started, mutation_started, unwinding = asyncio.Event(), asyncio.Event(), asyncio.Event()
    finish = asyncio.Event()
    calls = 0

    def clock() -> float:
        """Signal when the real mutation reaches its trusted clock before SQLite contention."""
        nonlocal calls
        calls += 1
        if calls == (2 if operation == "issue" else 1):
            mutation_started.set()
        return time.time()

    with EventStore(path) as store, sqlite3.connect(path, timeout=1) as blocker:
        control = ProtectedEnrollmentControl(
            hub=SynapseHub(journal=store, anti_rollback_checkpoint=False),
            sealed_keys=keys,
            clock=clock,
        )
        if operation == "revoke":
            await control.issue(enrollment)
        calls = 0
        mutation_started.clear()
        before = {enrollment.session_id: enrollment} if operation == "revoke" else {}
        after = {} if operation == "revoke" else {enrollment.session_id: enrollment}
        events = [ISSUE_EVENT_KIND]
        if operation == "revoke":
            events.append(REVOKE_EVENT_KIND)
        socket_path = Path("contended.sock")

        async def owner() -> None:
            """Own the real listener until its contended enrollment commands have drained."""
            servers = AsyncExitStack()
            try:
                async with retain_operator_connections(
                    control=control, operator_uid=os.geteuid()
                ) as handler:
                    server = await asyncio.start_unix_server(handler, path=socket_path)
                    servers.push_async_callback(server.wait_closed)
                    servers.callback(server.close)
                    started.set()
                    try:
                        await finish.wait()
                    finally:
                        unwinding.set()
            finally:
                await servers.aclose()

        task = asyncio.create_task(owner())
        peers = AsyncExitStack()
        try:
            await asyncio.wait_for(started.wait(), 2)
            blocker.execute("BEGIN IMMEDIATE")
            reader, writer = await asyncio.open_unix_connection(socket_path)
            peers.push_async_callback(writer.wait_closed)
            peers.callback(writer.close)
            metadata = {
                name: value
                for name, value in enrollment_issue_payload(enrollment).items()
                if name not in {"schema_version", "key_fingerprint"}
            }
            command: dict[str, object] = (
                {"op": "issue", "metadata": metadata}
                if operation == "issue"
                else {"op": "revoke", "session_id": enrollment.session_id}
            )
            raw = json.dumps(command).encode()
            writer.write(struct.pack("!I", len(raw)) + raw)
            await writer.drain()
            await asyncio.wait_for(mutation_started.wait(), 2)
            if interruption == "peer_eof":
                writer.close()
                await writer.wait_closed()
            else:
                task.cancel()
                await asyncio.wait_for(unwinding.wait(), 2)
                assert not task.done()
                if interruption == "owner_repeated_cancel":
                    done, _pending = await asyncio.wait({task}, timeout=0.1)
                    assert not done, "owner exited during its first append drain"
                    task.cancel()
                    done, _pending = await asyncio.wait({task}, timeout=0.1)
                    assert not done, "owner exited before its durable append drained"
            assert control.current() == before
            assert blocker.execute("SELECT COUNT(*) FROM events").fetchone() == (len(events) - 1,)
            blocker.rollback()
            deadline = time.monotonic() + 2
            while control.current() == before:
                assert time.monotonic() < deadline, "durable enrollment mutation not published"
                await asyncio.sleep(0.01)
            assert control.current() == after
            if interruption != "peer_eof":
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
                # A drained commit cannot be reported as a definite refusal.
                assert await asyncio.wait_for(reader.read(), 2) == b""
            else:
                finish.set()
                await asyncio.wait_for(task, 2)
            assert [event.kind for event in store.read_all()] == events
            assert store.read_operations() == ()
            with EventStore(path) as reopened:
                with pytest.raises(UnsupportedProtectedWriteHistoryError):
                    SynapseHub(journal=reopened, anti_rollback_checkpoint=False)
                restored = ProtectedEnrollmentControl(
                    hub=SynapseHub(
                        journal=reopened,
                        protected_write_policies={},
                        anti_rollback_checkpoint=False,
                    ),
                    sealed_keys=keys,
                    clock=time.time,
                )
                assert restored.current() == control.current()
                if operation == "revoke":
                    for replacement, refusal in (
                        (enrollment, "session id reused"),
                        (same_key, "key id reused"),
                        (same_secret, "key fingerprint reused"),
                    ):
                        with pytest.raises(ValueError, match=refusal):
                            await restored.issue(replacement)
                    assert restored.current() == {}
                    assert [event.kind for event in reopened.read_all()] == events
                    assert reopened.read_operations() == ()
        finally:
            blocker.rollback()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await peers.aclose()


@pytest.mark.parametrize(
    ("bound", "accepted"), [(True, False), (0, False), (1, True), (128, True), (129, False)]
)
@pytest.mark.asyncio
async def test_operator_capacity_bound_refuses_or_serves_an_actual_request(
    tmp_path: Path, bound: int, accepted: bool
) -> None:
    """Refuse invalid bounds and serve a real frame at both valid capacity endpoints.

    The actual same-UID Unix connection checks this component's request ownership
    and validation only. Dedicated deployment identities and root-owned listener
    custody remain separate native qualification requirements.
    """
    with EventStore(tmp_path / "limits.db") as store:
        control = ProtectedEnrollmentControl(
            hub=SynapseHub(journal=store, anti_rollback_checkpoint=False),
            sealed_keys={},
            clock=lambda: 1.0,
        )
        if not accepted:
            async with AsyncExitStack() as refused:
                with pytest.raises(ValueError, match="invalid operator connection bound"):
                    await refused.enter_async_context(
                        retain_operator_connections(
                            control=control, operator_uid=os.geteuid(), max_connections=bound
                        )
                    )
        else:
            async with retain_operator_connections(
                control=control, operator_uid=os.geteuid(), max_connections=bound
            ) as handler:
                path = Path("bounds.sock")
                server = await asyncio.start_unix_server(handler, path=path)
                async with server:
                    reader, writer = await asyncio.open_unix_connection(path)
                    try:
                        writer.write(struct.pack("!I", 2) + b"{}")
                        await writer.drain()
                        assert await asyncio.wait_for(reader.read(), 2) == b'{"ok":false}\n'
                    finally:
                        writer.close()
                        await writer.wait_closed()
        assert store.read_all() == []
        assert control.current() == {}


@pytest.mark.parametrize("exceptional_exit", [False, True])
@pytest.mark.asyncio
async def test_partial_command_capacity_and_shutdown_refuse_mutation(
    tmp_path: Path, exceptional_exit: bool
) -> None:
    """Excess and post-shutdown peers cannot complete an enrollment command."""
    with EventStore(tmp_path / "lifetime.db") as store:
        control = ProtectedEnrollmentControl(
            hub=SynapseHub(journal=store, anti_rollback_checkpoint=False),
            sealed_keys={},
            clock=lambda: 1.0,
        )
        path = Path("lifetime.sock")
        server: asyncio.AbstractServer | None = None
        writer: asyncio.StreamWriter | None = None
        reader: asyncio.StreamReader | None = None
        ready = asyncio.Event()
        owned = AsyncExitStack()
        try:
            try:
                async with retain_operator_connections(
                    control=control, operator_uid=os.geteuid(), max_connections=1
                ) as handler:

                    def accept(
                        accepted_reader: asyncio.StreamReader, accepted_writer: asyncio.StreamWriter
                    ) -> None:
                        """Accept the actual partial-command connection and signal ownership to the
                        test.
                        """
                        handler(accepted_reader, accepted_writer)
                        ready.set()

                    server = await asyncio.start_unix_server(accept, path=path)
                    owned.push_async_callback(server.wait_closed)
                    owned.callback(server.close)
                    reader, writer = await asyncio.open_unix_connection(path)
                    owned.push_async_callback(writer.wait_closed)
                    owned.callback(writer.close)
                    await asyncio.wait_for(ready.wait(), 2)
                    writer.write(b"\x00\x00")
                    await writer.drain()
                    excess_reader, excess_writer = await asyncio.open_unix_connection(path)
                    try:
                        assert await asyncio.wait_for(excess_reader.read(), 2) == b""
                    finally:
                        excess_writer.close()
                        await excess_writer.wait_closed()
                    assert store.read_all() == []
                    if exceptional_exit:
                        raise RuntimeError("authority scope ended")
            except RuntimeError as error:
                assert exceptional_exit and str(error) == "authority scope ended"
            assert reader is not None
            assert await asyncio.wait_for(reader.read(), 2) == b""
            assert server is not None and server.is_serving()
            late_reader, late_writer = await asyncio.open_unix_connection(path)
            try:
                assert await asyncio.wait_for(late_reader.read(), 2) == b""
            finally:
                late_writer.close()
                await late_writer.wait_closed()
            assert store.read_all() == []
            assert control.current() == {}
        finally:
            await owned.aclose()


@pytest.mark.asyncio
async def test_cancelling_authority_owner_drains_partial_command(tmp_path: Path) -> None:
    """Real owner-task cancellation closes a stalled request before store teardown."""
    with EventStore(tmp_path / "cancelled.db") as store:
        control = ProtectedEnrollmentControl(
            hub=SynapseHub(journal=store, anti_rollback_checkpoint=False),
            sealed_keys={},
            clock=lambda: 1.0,
        )
        path = Path("cancelled.sock")
        started, accepted = asyncio.Event(), asyncio.Event()
        hold = asyncio.Event()

        async def owner() -> None:
            """Retain the actual listener while cancellation unwinds its owned command scope."""
            servers = AsyncExitStack()
            try:
                async with retain_operator_connections(
                    control=control, operator_uid=os.geteuid()
                ) as handler:

                    def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                        """Hand the real accepted connection to its owner before releasing the test
                        barrier.
                        """
                        handler(reader, writer)
                        accepted.set()

                    server = await asyncio.start_unix_server(accept, path=path)
                    servers.push_async_callback(server.wait_closed)
                    servers.callback(server.close)
                    started.set()
                    await hold.wait()
            finally:
                await servers.aclose()

        task = asyncio.create_task(owner())
        peers = AsyncExitStack()
        try:
            await asyncio.wait_for(started.wait(), 2)
            reader, writer = await asyncio.open_unix_connection(path)
            peers.push_async_callback(writer.wait_closed)
            peers.callback(writer.close)
            await asyncio.wait_for(accepted.wait(), 2)
            writer.write(b"\x00")
            await writer.drain()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert await asyncio.wait_for(reader.read(), 2) == b""
            assert store.read_all() == []
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await peers.aclose()


@pytest.mark.asyncio
async def test_storage_failure_is_drained_and_logged_without_command_values(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A real closed journal cannot silently grant a revoke receipt."""
    store = EventStore(tmp_path / "failed.db")
    enrollment = _enrollment()
    hub = SynapseHub(journal=store, anti_rollback_checkpoint=False)
    control = ProtectedEnrollmentControl(
        hub=hub,
        sealed_keys={enrollment.session_id: enrollment.key},
        clock=lambda: 1_900_000_000.0,
    )
    try:
        await control.issue(enrollment)
        store.close()
        async with retain_operator_connections(
            control=control, operator_uid=os.geteuid()
        ) as handler:
            server = await asyncio.start_unix_server(handler, path=Path("failed.sock"))
            async with server:
                reader, writer = await asyncio.open_unix_connection(Path("failed.sock"))
                try:
                    command = json.dumps(
                        {"op": "revoke", "session_id": enrollment.session_id}
                    ).encode()
                    writer.write(struct.pack("!I", len(command)) + command)
                    await writer.drain()
                    assert await asyncio.wait_for(reader.read(), 2) == b""
                finally:
                    writer.close()
                    await writer.wait_closed()
        assert caplog.messages == ["protected operator request failed"]
        assert not hub.state.protected_enrollments[enrollment.session_id].revoked
    finally:
        store.close()
