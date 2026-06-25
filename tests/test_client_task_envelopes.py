# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real WebSocket tests for client task envelopes

from __future__ import annotations

from client_helpers import connected_recording_agent, wait_for_recorded_count


async def test_claim_sends_worktree_and_paths() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.claim("T1", note="n", worktree="wt", paths=["src", "tests"])
        await wait_for_recorded_count(messages, 2)
        sent = messages[-1]

    assert sent["type"] == "claim"
    assert sent["worktree"] == "wt"
    assert sent["paths"] == ["src", "tests"]


async def test_claim_omits_scope_when_unset() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.claim("T1")
        await wait_for_recorded_count(messages, 2)
        sent = messages[-1]

    assert "worktree" not in sent
    assert "paths" not in sent
    assert "git" not in sent


async def test_claim_sends_git_context() -> None:
    git = {"branch": "feature/x", "base": "main", "auto_release_on": "merge"}
    async with connected_recording_agent("A") as (agent, messages):
        await agent.claim("T1", paths=["src"], git=git)
        await wait_for_recorded_count(messages, 2)
        sent = messages[-1]

    assert sent["git"] == git


async def test_release_sends_and_omits_epoch() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.release("T1", epoch=4)
        await wait_for_recorded_count(messages, 2)
        with_epoch = messages[-1]
        await agent.release("T1")
        await wait_for_recorded_count(messages, 3)
        without_epoch = messages[-1]

    assert with_epoch["epoch"] == 4
    assert "epoch" not in without_epoch


async def test_claim_and_release_send_idem_key() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.claim("T1", idem_key="k1")
        await wait_for_recorded_count(messages, 2)
        claim = messages[-1]
        await agent.release("T1", idem_key="k2")
        await wait_for_recorded_count(messages, 3)
        release = messages[-1]

    assert claim["idem_key"] == "k1"
    assert release["idem_key"] == "k2"


async def test_idem_key_omitted_when_unset() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.claim("T1")
        await wait_for_recorded_count(messages, 2)
        sent = messages[-1]

    assert "idem_key" not in sent


async def test_request_resume_sends_cursor() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.request_resume(since=7)
        await wait_for_recorded_count(messages, 2)
        sent = messages[-1]

    assert sent["type"] == "resume_request"
    assert sent["since"] == 7


async def test_update_task_sends_lifecycle_and_cas_fields() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.update_task(
            "T1",
            status="working",
            note="n",
            data_ref="r",
            epoch=5,
            expected_version=2,
            idem_key="k",
        )
        await wait_for_recorded_count(messages, 2)
        sent = messages[-1]

    assert sent["type"] == "task_update"
    assert sent["status"] == "working"
    assert sent["epoch"] == 5
    assert sent["expected_version"] == 2
    assert sent["idem_key"] == "k"


async def test_update_task_minimal_omits_optional_fields() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.update_task("T1")
        await wait_for_recorded_count(messages, 2)
        sent = messages[-1]

    assert sent["task_id"] == "T1"
    assert "status" not in sent
    assert "expected_version" not in sent


async def test_request_wait_sends_task_id() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.request_wait("  T1  ")
        await wait_for_recorded_count(messages, 2)
        sent = messages[-1]

    assert sent["type"] == "wait_request"
    assert sent["task_id"] == "T1"


async def test_handoff_sends_full_and_minimal_envelopes() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.handoff("  T1 ", "  B ", note="over to you", epoch=3, idem_key="k")
        await wait_for_recorded_count(messages, 2)
        full = messages[-1]
        await agent.handoff("T1", "B")
        await wait_for_recorded_count(messages, 3)
        minimal = messages[-1]

    assert full["type"] == "handoff"
    assert full["task_id"] == "T1"
    assert full["to_agent"] == "B"
    assert full["note"] == "over to you"
    assert full["epoch"] == 3
    assert full["idem_key"] == "k"
    assert "note" not in minimal
    assert "epoch" not in minimal
    assert "idem_key" not in minimal


async def test_save_checkpoint_sends_full_and_minimal_envelopes() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.save_checkpoint("  T1 ", "cursor=5", epoch=2, idem_key="k")
        await wait_for_recorded_count(messages, 2)
        full = messages[-1]
        await agent.save_checkpoint("T1", "x")
        await wait_for_recorded_count(messages, 3)
        minimal = messages[-1]

    assert full["type"] == "checkpoint"
    assert full["task_id"] == "T1"
    assert full["checkpoint"] == "cursor=5"
    assert full["epoch"] == 2
    assert full["idem_key"] == "k"
    assert "epoch" not in minimal
    assert "idem_key" not in minimal
