# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — every refused release is recorded in the hub log with its operation
"""A refused release leaves one hub log line naming sender, task and operation.

A sender that hears nothing reports its release as unknown. The hub log is
where an operator tells such a refusal from a frame that never arrived. Each
test drives a real in-process hub over its WebSocket through one refusal path.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.acl import CLAIM, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType

_FIELDS_SENT_BY_SEND_MESSAGE = {"sender", "type", "target", "payload", "timestamp"}


def _is(msg_type: str) -> Callable[[dict[str, Any]], bool]:
    return lambda message: message.get("type") == msg_type


def _refusals(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "synapse.hub" and record.getMessage().startswith("Release refused")
    ]


async def _claim(handle: AgentHandle, task_id: str) -> None:
    await handle.agent.claim(task_id, worktree="/repo", paths=[task_id])
    await handle.recorder.wait_for(
        lambda m: m.get("type") == MessageType.CLAIM_GRANTED and m.get("task_id") == task_id
    )


async def _send_release(handle: AgentHandle, task_id: str, key: str, **extra: Any) -> None:
    request = handle.agent.prepare_release(task_id, idem_key=key, **extra)
    await handle.agent.send_message(
        MessageType.RELEASE,
        target=request["target"],
        payload=request["payload"],
        **{k: v for k, v in request.items() if k not in _FIELDS_SENT_BY_SEND_MESSAGE},
    )


async def test_a_denied_keyed_and_legacy_release_are_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The handler's own denial names the operation, or None for a legacy release."""
    caplog.set_level(logging.WARNING, logger="synapse.hub")
    async with running_hub(SynapseHub()) as (_hub, uri):
        alice = await connect_agent("P/alice", uri)
        mallory = await connect_agent("P/mallory", uri)
        try:
            await _claim(alice, "T1")
            await _send_release(mallory, "T1", "op-denied")
            await mallory.recorder.wait_for(_is(MessageType.RELEASE_DENIED))
            mallory.recorder.messages.clear()
            await mallory.agent.send_message(MessageType.RELEASE, target="System", task_id="T1")
            await mallory.recorder.wait_for(_is(MessageType.RELEASE_DENIED))
            mallory.recorder.messages.clear()
            await mallory.agent.save_checkpoint("T1", "not a release")
            await mallory.recorder.wait_for(_is(MessageType.CHECKPOINT_DENIED))
        finally:
            await close_agents(alice, mallory)
    first, second = _refusals(caplog)
    assert first.startswith("Release refused for P/mallory on task 'T1' (operation 'op-denied'): ")
    assert second.startswith("Release refused for P/mallory on task 'T1' (operation None): ")


async def test_a_strict_fencing_refusal_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="synapse.hub")
    async with running_hub(SynapseHub(require_fencing_epoch=True)) as (_hub, uri):
        alice = await connect_agent("P/alice", uri)
        mallory = await connect_agent("P/mallory", uri)
        try:
            await _claim(alice, "T2")
            await _send_release(mallory, "T2", "op-unfenced")
            await mallory.recorder.wait_for(_is(MessageType.RELEASE_DENIED))
        finally:
            await close_agents(alice, mallory)
    [line] = _refusals(caplog)
    assert line.startswith("Release refused for P/mallory on task 'T2' (operation 'op-unfenced'): ")
    assert "Fencing epoch required" in line


async def test_an_idempotency_conflict_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Reusing a granted release's key for a different request is a logged refusal."""
    caplog.set_level(logging.WARNING, logger="synapse.hub")
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(SynapseHub(journal=journal)) as (_hub, uri):
            alice = await connect_agent("P/alice", uri)
            try:
                await _claim(alice, "T3")
                await _send_release(alice, "T3", "op-reused")
                await alice.recorder.wait_for(_is(MessageType.RELEASE_GRANTED))
                await _claim(alice, "T3")
                alice.recorder.messages.clear()
                await _send_release(alice, "T3", "op-reused", confidence="high")
                conflict = await alice.recorder.wait_for(_is(MessageType.ERROR))
            finally:
                await close_agents(alice)
    assert conflict["error_code"] == "idempotency_conflict"
    assert _refusals(caplog) == [
        "Release refused for P/alice on task 'T3' (operation 'op-reused'): 'idempotency conflict'"
    ]


async def test_a_conflict_with_a_durable_release_is_logged_after_a_restart(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A restarted hub finds the reused key in its journal and logs the refusal."""
    caplog.set_level(logging.WARNING, logger="synapse.hub")
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(SynapseHub(journal=journal)) as (_hub, uri):
            alice = await connect_agent("P/alice", uri)
            try:
                await _claim(alice, "T7")
                await _send_release(alice, "T7", "op-durable")
                await alice.recorder.wait_for(_is(MessageType.RELEASE_GRANTED))
            finally:
                await close_agents(alice)
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(SynapseHub(journal=journal)) as (_hub, uri):
            alice = await connect_agent("P/alice", uri)
            try:
                await _claim(alice, "T7")
                alice.recorder.messages.clear()
                await _send_release(alice, "T7", "op-durable", confidence="high")
                conflict = await alice.recorder.wait_for(_is(MessageType.ERROR))
            finally:
                await close_agents(alice)
    assert conflict["error_code"] == "idempotency_conflict"
    assert _refusals(caplog) == [
        "Release refused for P/alice on task 'T7' (operation 'op-durable'): 'idempotency conflict'"
    ]


async def test_an_acl_refusal_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="synapse.hub")
    policy = AclPolicy([AclRule(CLAIM, "claim", "*"), AclRule(CLAIM, "path", "*")])
    async with running_hub(SynapseHub(acl_policy=policy, require_acl=True)) as (_hub, uri):
        alice = await connect_agent("P/alice", uri)
        try:
            await _claim(alice, "T4")
            await _send_release(alice, "T4", "op-acl")
            await alice.recorder.wait_for(_is(MessageType.ERROR))
        finally:
            await close_agents(alice)
    [line] = _refusals(caplog)
    assert line.startswith("Release refused for P/alice on task 'T4' (operation 'op-acl'): ")
    assert "access denied" in line


async def test_a_per_message_auth_refusal_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="synapse.hub")
    async with running_hub(SynapseHub(require_per_message_auth=True)) as (_hub, uri):
        alice = await connect_agent("P/alice", uri)
        try:
            await _send_release(alice, "T5", "op-unsigned")
            await alice.recorder.wait_for(
                lambda m: m.get("type") == MessageType.ERROR and m.get("target") == "P/alice"
            )
        finally:
            await close_agents(alice)
    assert _refusals(caplog) == [
        "Release refused for P/alice on task 'T5' (operation 'op-unsigned'): "
        "'per-message authentication failed: missing'"
    ]


async def test_frame_text_cannot_forge_a_log_line(caplog: pytest.LogCaptureFixture) -> None:
    """A task id with a line break is written escaped, on the one refusal line."""
    caplog.set_level(logging.WARNING, logger="synapse.hub")
    async with running_hub(SynapseHub()) as (_hub, uri):
        mallory = await connect_agent("P/mallory", uri)
        try:
            await mallory.agent.send_message(
                MessageType.RELEASE,
                target="System",
                task_id="T6\nRelease refused for P/alice",
                idem_key="op\nforged",
            )
            await mallory.recorder.wait_for(_is(MessageType.RELEASE_DENIED))
        finally:
            await close_agents(mallory)
    [line] = _refusals(caplog)
    assert "\n" not in line
    assert "'T6\\nRelease refused for P/alice'" in line and "'op\\nforged'" in line
