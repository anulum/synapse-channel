# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real A2A execution outcome boundaries
"""Exercise HTTP, durable state, real hub traffic and independent native writers."""

from __future__ import annotations

import asyncio
import http.client
import json
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.a2a_errors import A2ANotFoundError, A2AStoreError
from synapse_channel.a2a_http import make_a2a_http_server
from synapse_channel.a2a_outcome import OUTCOME_KEY
from synapse_channel.a2a_server import A2ABridge, SynapseAgentRuntime
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.owner_registration import close_owner, register_owner
from synapse_channel.execution_outcome import ExecutionOutcome, OutcomeState


@pytest.mark.parametrize(
    "invalid",
    [
        {"operation_id": ""},
        {"boundary": "external_authority"},
        {"state": "started"},
        {"stop_requested": 1},
    ],
)
def test_public_observation_api_refuses_ambiguous_dynamic_input(invalid: dict[str, Any]) -> None:
    """The documented Python value rejects invalid fields from a dynamic client."""
    fields: dict[str, Any] = {
        "operation_id": "public-api-request",
        "boundary": "a2a_exchange",
        "state": OutcomeState.STARTED,
        "observation": "public_client_observation",
    }
    fields.update(invalid)
    with pytest.raises(ValueError):
        ExecutionOutcome(**fields)


def http_json(
    port: int,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    version: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Use the actual HTTP edge without blocking the hub's event loop."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(
            method,
            path,
            body=json.dumps(body) if body is not None else None,
            headers={
                "Content-Type": "application/json",
                **({"A2A-Version": version} if version is not None else {}),
            },
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def request_message(task_id: str) -> dict[str, Any]:
    """Build a real task-bound work request with an exact stable message id."""
    return {
        "taskId": task_id,
        "contextId": "context-" + task_id,
        "messageId": "message-" + task_id,
        "role": "ROLE_USER",
        "parts": [{"text": "native fixture work"}],
    }


@asynccontextmanager
async def live_bridge(
    state_file: Path,
) -> AsyncIterator[tuple[A2ABridge, AgentHandle, int]]:
    """Join every owned server, client and event-loop thread before returning."""
    async with running_hub() as (_hub, uri):
        worker = await connect_agent("outcome-worker", uri)
        bridge: A2ABridge

        async def on_frame(frame: dict[str, Any]) -> None:
            """Route actual decoded sender frames through the public bridge."""
            bridge.handle_synapse_frame(frame)

        sender = SynapseAgent("outcome-sender", on_frame, uri=uri, verbose=False)
        runtime = SynapseAgentRuntime(sender)
        try:
            bridge = A2ABridge(
                agent=sender,
                agent_card={"url": "http://127.0.0.1"},
                target="outcome-worker",
                store=A2ATaskStore(state_file),
                submit=runtime.run,
            )
            server = make_a2a_http_server(bridge=bridge, host="127.0.0.1", port=0)
            bridge.allowed_authorities = (f"127.0.0.1:{server.server_address[1]}",)
            thread = threading.Thread(
                target=lambda: server.serve_forever(poll_interval=0.02), daemon=True
            )
            try:
                assert await asyncio.to_thread(runtime.start)
                thread.start()
                yield bridge, worker, int(server.server_address[1])
            finally:
                if thread.is_alive():
                    await asyncio.to_thread(server.shutdown)
                    await asyncio.to_thread(thread.join, 3)
                    assert not thread.is_alive()
                server.server_close()
        finally:
            await asyncio.to_thread(runtime.stop)
            await close_agents(worker)


@asynccontextmanager
async def effect_worker(marker: Path) -> AsyncIterator[asyncio.subprocess.Process]:
    """Hold an actual writer on its input until after a local task transition."""
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import pathlib,sys; print('ready',flush=True); "
        "sys.stdin.readline(); pathlib.Path(sys.argv[1]).write_bytes(b'effect\\n')",
        str(marker),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert child.stdout is not None
        assert await asyncio.wait_for(child.stdout.readline(), 3) == b"ready\n"
        yield child
    finally:
        if child.returncode is None:
            child.kill()
        await child.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["cancel", "reload"])
async def test_local_terminal_state_does_not_confirm_remote_stop(
    tmp_path: Path, transition: str
) -> None:
    """Canceled/failed protocol rows expose unknown remote work that still writes."""
    state_file = tmp_path / "state.json"
    async with live_bridge(state_file) as (bridge, worker, port):
        missing_status, _ = await asyncio.to_thread(http_json, port, "POST", "/tasks/absent:cancel")
        assert missing_status == 404 and bridge.get_task("absent") is None
        status, response = await asyncio.to_thread(
            http_json,
            port,
            "POST",
            "/message:send",
            {"message": request_message(transition)},
        )
        assert status == 200
        task = response["task"]
        await worker.recorder.wait_for(
            lambda row: (
                row.get("type") == "chat" and row.get("metadata", {}).get("a2aTaskId") == transition
            )
        )
        initial = task["metadata"][OUTCOME_KEY]
        assert initial["state"] == "started"
        marker = tmp_path / "actual-effect"
        async with effect_worker(marker) as child:
            if transition == "cancel":
                status, task = await asyncio.to_thread(
                    http_json, port, "POST", f"/tasks/{transition}:cancel"
                )
                assert status == 200
                assert task["status"]["state"] == "TASK_STATE_CANCELED"
                assert (await asyncio.to_thread(http_json, port, "GET", f"/tasks/{transition}"))[1][
                    "metadata"
                ][OUTCOME_KEY] == task["metadata"][OUTCOME_KEY]
            else:
                recovered = A2ATaskStore(state_file).get(transition)
                assert recovered is not None
                task = recovered
                assert task["status"]["state"] == "TASK_STATE_FAILED"
            outcome = task["metadata"][OUTCOME_KEY]
            assert outcome["operation_id"] == initial["operation_id"]
            assert outcome["state"] == outcome["external_effect"] == "unknown"
            assert (
                outcome["observation"]
                == {"cancel": "local_task_cancelled", "reload": "bridge_restart"}[transition]
            )
            assert outcome["recovery"] == "inquire_or_operator"
            assert not outcome["automatic_retry"]
            assert not outcome["stop_requested"] and not outcome["stop_confirmed"]
            assert not outcome["effects_undone"]
            assert child.returncode is None and not marker.exists()
            assert child.stdin is not None
            child.stdin.write(b"finish own fixture\n")
            await child.stdin.drain()
            child.stdin.close()
            assert await asyncio.wait_for(child.wait(), 3) == 0
            assert marker.read_bytes() == b"effect\n"
        if transition == "cancel":
            events = bridge.subscribe_task_events(transition)
            assert events is not None
            assert events[-1]["task"]["metadata"][OUTCOME_KEY] == outcome


@pytest.mark.asyncio
async def test_durable_intent_precedes_real_send_and_reply_is_scoped(tmp_path: Path) -> None:
    """A real peer sees durable intent; its reply confirms exchange, not business."""
    state_file = tmp_path / "state.json"
    async with live_bridge(state_file) as (bridge, worker, port):
        send = asyncio.create_task(
            asyncio.to_thread(
                http_json,
                port,
                "POST",
                "/message:send",
                {"message": request_message("replied")},
            )
        )
        request = await worker.recorder.wait_for(
            lambda row: (
                row.get("type") == "chat" and row.get("metadata", {}).get("a2aTaskId") == "replied"
            )
        )
        durable = json.loads(state_file.read_text())["tasks"]["replied"]
        assert durable["metadata"][OUTCOME_KEY]["state"] == "started"
        assert await asyncio.to_thread(bridge.expire_timed_out_tasks, now=time.time()) == []
        assert json.loads(state_file.read_text())["tasks"]["replied"] == durable
        await worker.agent.chat(
            "peer response", target="outcome-sender", metadata=request["metadata"]
        )
        status, _response = await send
        assert status == 200

        async def completed() -> dict[str, Any]:
            """Read the real HTTP task until its correlated response is recorded."""
            while True:
                code, task = await asyncio.to_thread(http_json, port, "GET", "/tasks/replied")
                assert code == 200
                if task["status"]["state"] == "TASK_STATE_COMPLETED":
                    return task
                await asyncio.sleep(0.01)

        task = await asyncio.wait_for(completed(), 3)
        outcome = task["metadata"][OUTCOME_KEY]
        assert outcome["boundary"] == "a2a_exchange"
        assert outcome["state"] == "confirmed_done"
        assert outcome["observation"] == "correlated_reply_received"
        assert outcome["external_effect"] == "unknown"
        assert outcome["external_recovery"] == "inquire_or_operator"
        assert not outcome["automatic_retry"] and not outcome["effects_undone"]
        events = bridge.subscribe_task_events("replied")
        assert events is not None
        assert events[-1]["task"]["metadata"][OUTCOME_KEY] == outcome


@pytest.mark.asyncio
async def test_real_storage_refusal_precedes_forwarding(tmp_path: Path) -> None:
    """An actual invalid parent refuses persistence before any work is forwarded."""
    blocked = tmp_path / "parent-is-file"
    blocked.write_bytes(b"owned fixture")
    async with live_bridge(blocked / "state.json") as (bridge, worker, _port):
        with pytest.raises(FileExistsError):
            await asyncio.to_thread(bridge.create_working_task, request_message("not-forwarded"))
        assert bridge.store.get("not-forwarded") is None
        assert blocked.read_bytes() == b"owned fixture"
        assert not [
            row
            for row in worker.recorder.messages
            if row.get("metadata", {}).get("a2aTaskId") == "not-forwarded"
        ]


async def completed_task(port: int, task_id: str) -> dict[str, Any]:
    """Await actual HTTP completion with a finite caller deadline."""

    async def observe() -> dict[str, Any]:
        """Read task state without repeating a work request."""
        while True:
            code, task = await asyncio.to_thread(http_json, port, "GET", f"/tasks/{task_id}")
            assert code == 200
            if task["status"]["state"] == "TASK_STATE_COMPLETED":
                return task
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(observe(), 3)


@pytest.mark.asyncio
async def test_embedding_continuation_refuses_a_pruned_native_task(tmp_path: Path) -> None:
    """A retained caller snapshot cannot resurrect work after actual terminal retention."""
    async with live_bridge(tmp_path / "state.json") as (bridge, worker, port):
        message = request_message("retained-snapshot")
        code, result = await asyncio.to_thread(
            http_json, port, "POST", "/message:send", {"message": message}
        )
        assert code == 200
        await worker.recorder.wait_for(
            lambda row: row.get("metadata", {}).get("a2aTaskId") == "retained-snapshot"
        )
        assert bridge.cancel_task("retained-snapshot") is not None
        assert bridge.store.prune_expired(now=time.time() + bridge.store.retention_seconds + 1) == [
            "retained-snapshot"
        ]
        with pytest.raises(A2ANotFoundError):
            await asyncio.to_thread(
                bridge.execution.send,
                result["task"],
                {**message, "messageId": "delayed-continuation"},
                target="outcome-worker",
                continue_existing=True,
            )
        assert bridge.get_task("retained-snapshot") is None
        assert len([row for row in worker.recorder.messages if row.get("type") == "chat"]) == 1


@pytest.mark.parametrize("transition", ["cancel", "expire", "expire_legacy"])
@pytest.mark.asyncio
async def test_native_terminal_transition_survives_forward_ack_failure(
    tmp_path: Path, transition: str
) -> None:
    """Cancellation or timeout remains terminal when a real forwarding adapter then fails I/O."""
    blocked = tmp_path / "file-instead-of-directory"
    blocked.write_text("owned fixture")
    settled = asyncio.Event()
    late_received = asyncio.Event()
    loop = asyncio.get_running_loop()
    bridge: A2ABridge

    def submit(coro: Coroutine[Any, Any, Any]) -> Any:
        """Send natively, await the concurrent local transition, then hit actual I/O refusal."""
        asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=3)
        asyncio.run_coroutine_threadsafe(settled.wait(), loop).result(timeout=3)
        return (blocked / "acknowledgement").write_text("actual filesystem refusal")

    async def receive(frame: dict[str, Any]) -> None:
        """Route actual late peer traffic through the public bridge."""
        bridge.handle_synapse_frame(frame)
        if frame.get("payload") == "late peer reply":
            late_received.set()

    async with running_hub() as (_hub, uri):
        worker = await connect_agent("outcome-worker", uri)
        sender = SynapseAgent("outcome-adapter", receive, uri=uri, verbose=False)
        bridge = A2ABridge(
            agent=sender,
            agent_card={},
            target="outcome-worker",
            store=A2ATaskStore(tmp_path / "state.json"),
            task_timeout_seconds=60,
            submit=submit,
        )
        listener = await register_owner(sender, 3)
        assert listener is not None
        job = asyncio.create_task(
            asyncio.to_thread(bridge.create_working_task, request_message("terminal-forward"))
        )
        try:
            request = await worker.recorder.wait_for(
                lambda row: row.get("metadata", {}).get("a2aTaskId") == "terminal-forward"
            )
            if transition == "expire_legacy":
                legacy = bridge.store.get("terminal-forward")
                assert legacy is not None
                bridge.store.put({**legacy, "metadata": None})
            if transition == "cancel":
                task = await asyncio.to_thread(bridge.cancel_task, "terminal-forward")
                expected = "TASK_STATE_CANCELED"
            else:
                tasks = await asyncio.to_thread(bridge.expire_timed_out_tasks, now=time.time() + 61)
                assert len(tasks) == 1
                task = tasks[0]
                expected = "TASK_STATE_FAILED"
            assert task is not None and task["status"]["state"] == expected
            settled.set()
            with pytest.raises(NotADirectoryError):
                await asyncio.wait_for(job, 3)
            retained = bridge.get_task("terminal-forward")
            assert retained == task
            assert retained["metadata"][OUTCOME_KEY]["state"] == "unknown"
            assert not retained["metadata"][OUTCOME_KEY]["stop_confirmed"]
            await worker.agent.chat(
                "late peer reply", target="outcome-adapter", metadata=request["metadata"]
            )
            await asyncio.wait_for(late_received.wait(), 3)
            assert bridge.get_task("terminal-forward") == task
            assert len([row for row in worker.recorder.messages if row.get("type") == "chat"]) == 1
            assert blocked.read_text() == "owned fixture"
        finally:
            settled.set()
            await asyncio.gather(job, return_exceptions=True)
            await close_owner(sender, listener)
            await close_agents(worker)


@pytest.mark.asyncio
async def test_real_http_sse_and_task_subscription_preserve_scoped_outcomes(tmp_path: Path) -> None:
    """The actual stream and replay endpoints retain the same native exchange observation."""
    async with live_bridge(tmp_path / "state.json") as (_bridge, worker, port):

        def stream(path: str, body: dict[str, Any]) -> list[dict[str, Any]]:
            """Read one complete bounded SSE response from the actual HTTP server."""
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                connection.request(
                    "POST",
                    path,
                    body=json.dumps(body),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                assert response.status == 200
                assert response.getheader("Content-Type", "").startswith("text/event-stream")
                return [
                    json.loads(line.removeprefix("data:").strip())
                    for line in response.read().decode().splitlines()
                    if line.startswith("data:")
                ]
            finally:
                connection.close()

        first = await asyncio.to_thread(
            stream, "/message:stream", {"message": request_message("streamed")}
        )
        assert first
        observed = first[-1]["task"]["metadata"][OUTCOME_KEY]
        assert observed["state"] == "started" and observed["external_effect"] == "unknown"
        replay = await asyncio.to_thread(stream, "/tasks/streamed:subscribe", {})
        assert replay[-1]["task"]["metadata"][OUTCOME_KEY] == observed
        request = await worker.recorder.wait_for(
            lambda row: row.get("metadata", {}).get("a2aTaskId") == "streamed"
        )
        await worker.agent.chat(
            "streamed peer reply", target="outcome-sender", metadata=request["metadata"]
        )
        task = await completed_task(port, "streamed")
        outcome = task["metadata"][OUTCOME_KEY]
        assert outcome["operation_id"] == observed["operation_id"]
        assert outcome["state"] == "confirmed_done" and not outcome["automatic_retry"]
        assert len([r for r in worker.recorder.messages if r.get("type") == "chat"]) == 1


@pytest.mark.asyncio
async def test_native_concurrent_reply_cannot_replace_a_new_exchange(tmp_path: Path) -> None:
    """Actual peer replies race HTTP continuations without changing their operation identity."""

    def matching_request(
        task_id: str, previous: str | None = None
    ) -> Callable[[dict[str, Any]], bool]:
        """Bind one awaited peer request independently of subsequent iterations."""
        return lambda row: (
            row.get("metadata", {}).get("a2aTaskId") == task_id
            and (previous is None or row.get("metadata", {}).get("a2aOperationId") != previous)
        )

    interval = sys.getswitchinterval()
    sys.setswitchinterval(0.000001)
    try:
        async with live_bridge(tmp_path / "state.json") as (_bridge, worker, port):
            for index in range(24):
                task_id = f"concurrent-{index}"
                initial = request_message(task_id)
                code, _ = await asyncio.to_thread(
                    http_json, port, "POST", "/message:send", {"message": initial}
                )
                assert code == 200
                first = await worker.recorder.wait_for(matching_request(task_id))
                message = {
                    **initial,
                    "messageId": f"followup-{index}",
                    "parts": [{"text": "native concurrent input " * 350}],
                }
                continuation = asyncio.create_task(
                    asyncio.to_thread(
                        http_json,
                        port,
                        "POST",
                        "/message:send",
                        {"message": message},
                        version="1.0",
                    )
                )
                await asyncio.sleep((index % 3) * 0.0001)
                await worker.agent.chat(
                    "previous exchange reply", target="outcome-sender", metadata=first["metadata"]
                )
                code, result = await continuation
                assert code in {200, 409}
                if code == 409:
                    retained = await completed_task(port, task_id)
                    assert (
                        retained["metadata"][OUTCOME_KEY]["operation_id"]
                        == first["metadata"]["a2aOperationId"]
                    )
                    continue
                second = await worker.recorder.wait_for(
                    matching_request(task_id, str(first["metadata"]["a2aOperationId"]))
                )
                expected = second["metadata"]["a2aOperationId"]
                assert result["task"]["metadata"][OUTCOME_KEY]["operation_id"] == expected
                await worker.agent.chat(
                    "new exchange reply", target="outcome-sender", metadata=second["metadata"]
                )
                retained = await completed_task(port, task_id)
                assert retained["metadata"][OUTCOME_KEY]["operation_id"] == expected
                assert retained["status"]["message"]["parts"][0]["text"] == "new exchange reply"
    finally:
        sys.setswitchinterval(interval)


@pytest.mark.asyncio
async def test_duplicate_and_stale_continuation_do_not_repeat_or_complete_work(
    tmp_path: Path,
) -> None:
    """Actual HTTP duplicates refuse and a delayed first reply cannot finish the next exchange."""
    async with live_bridge(tmp_path / "state.json") as (_bridge, worker, port):
        message = request_message("continued")
        assert (
            await asyncio.to_thread(http_json, port, "POST", "/message:send", {"message": message})
        )[0] == 200
        first = await worker.recorder.wait_for(
            lambda row: row.get("metadata", {}).get("a2aTaskId") == "continued"
        )
        for version in (None, "1.0"):
            code, _ = await asyncio.to_thread(
                http_json, port, "POST", "/message:send", {"message": message}, version=version
            )
            assert code == 409
        followup = {**message, "messageId": "distinct-followup"}
        code, response = await asyncio.to_thread(
            http_json, port, "POST", "/message:send", {"message": followup}, version="1.0"
        )
        assert code == 200
        second = await worker.recorder.wait_for(
            lambda row: (
                row.get("metadata", {}).get("a2aTaskId") == "continued"
                and row.get("metadata", {}).get("a2aOperationId")
                != first["metadata"]["a2aOperationId"]
            )
        )
        current_id = response["task"]["metadata"][OUTCOME_KEY]["operation_id"]
        assert second["metadata"]["a2aOperationId"] == current_id
        await worker.agent.chat("stale reply", target="outcome-sender", metadata=first["metadata"])
        await worker.agent.chat(
            "current reply", target="outcome-sender", metadata=second["metadata"]
        )
        task = await completed_task(port, "continued")
        assert task["status"]["message"]["parts"][0]["text"] == "current reply"
        assert task["metadata"][OUTCOME_KEY]["operation_id"] == current_id
        assert task["metadata"][OUTCOME_KEY]["state"] == "confirmed_done"
        assert len([r for r in worker.recorder.messages if r.get("type") == "chat"]) == 2


@pytest.mark.asyncio
async def test_legacy_task_reply_does_not_claim_exact_exchange_confirmation(tmp_path: Path) -> None:
    """A peer without the new operation echo retains compatible task status and unknown outcome."""
    async with live_bridge(tmp_path / "state.json") as (_bridge, worker, port):
        assert (
            await asyncio.to_thread(
                http_json, port, "POST", "/message:send", {"message": request_message("legacy")}
            )
        )[0] == 200
        request = await worker.recorder.wait_for(
            lambda row: row.get("metadata", {}).get("a2aTaskId") == "legacy"
        )
        metadata = {k: v for k, v in request["metadata"].items() if k != "a2aOperationId"}
        await worker.agent.chat("legacy reply", target="outcome-sender", metadata=metadata)
        task = await completed_task(port, "legacy")
        outcome = task["metadata"][OUTCOME_KEY]
        assert outcome["state"] == "unknown"
        assert outcome["observation"] == "reply_operation_unconfirmed"
        assert not outcome["automatic_retry"]


@pytest.mark.asyncio
@pytest.mark.parametrize("after_reply", ["io_failure", "prune_failure", "prune_return"])
async def test_actual_adapter_io_and_retention_after_native_reply(
    tmp_path: Path, after_reply: str
) -> None:
    """An embedding adapter sees a real reply, then fails I/O or expires its retained task."""
    blocked = tmp_path / "file-instead-of-directory"
    blocked.write_text("owned fixture")
    completed = asyncio.Event()
    store = A2ATaskStore(tmp_path / "state.json", retention_seconds=60)
    loop = asyncio.get_running_loop()
    bridge: A2ABridge

    async def receive(frame: dict[str, Any]) -> None:
        """Deliver actual peer traffic and observe the resulting public task state."""
        bridge.handle_synapse_frame(frame)
        if frame.get("type") == "chat":
            task = bridge.get_task("adapter-reply")
            if task is not None and task["status"]["state"] == "TASK_STATE_COMPLETED":
                completed.set()

    def submit(coro: Coroutine[Any, Any, Any]) -> Any:
        """Run native forwarding before this adapter's own acknowledgement I/O."""
        result = asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=3)
        asyncio.run_coroutine_threadsafe(completed.wait(), loop).result(timeout=3)
        if after_reply.startswith("prune"):
            assert store.prune_expired(now=time.time() + 61) == ["adapter-reply"]
        if after_reply.endswith("failure"):
            (blocked / "acknowledgement").write_text("actual filesystem refusal")
        return result

    async with running_hub() as (_hub, uri):
        worker = await connect_agent("outcome-worker", uri)
        sender = SynapseAgent("outcome-adapter", receive, uri=uri, verbose=False)
        bridge = A2ABridge(
            agent=sender, agent_card={}, target="outcome-worker", store=store, submit=submit
        )
        listener = await register_owner(sender, 3)
        assert listener is not None

        async def reply() -> None:
            """Echo the real request's operation identity on its native peer socket."""
            request = await worker.recorder.wait_for(
                lambda row: row.get("metadata", {}).get("a2aTaskId") == "adapter-reply"
            )
            await worker.agent.chat(
                "actual peer reply", target="outcome-adapter", metadata=request["metadata"]
            )

        reply_job = asyncio.create_task(reply())
        try:
            if after_reply == "io_failure":
                task = await asyncio.to_thread(
                    bridge.create_working_task, request_message("adapter-reply")
                )
                assert task["status"]["state"] == "TASK_STATE_COMPLETED"
                assert task["metadata"][OUTCOME_KEY]["state"] == "confirmed_done"
                assert task["metadata"][OUTCOME_KEY]["external_effect"] == "unknown"
            else:
                error = NotADirectoryError if after_reply == "prune_failure" else A2AStoreError
                with pytest.raises(error):
                    await asyncio.to_thread(
                        bridge.create_working_task, request_message("adapter-reply")
                    )
                assert store.get("adapter-reply") is None
            await asyncio.wait_for(reply_job, 3)
            assert len([r for r in worker.recorder.messages if r.get("type") == "chat"]) == 1
            assert blocked.read_text() == "owned fixture"
        finally:
            if not reply_job.done():
                reply_job.cancel()
            await asyncio.gather(reply_job, return_exceptions=True)
            await close_owner(sender, listener)
            await close_agents(worker)
