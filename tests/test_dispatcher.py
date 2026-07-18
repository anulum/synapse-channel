# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the opt-in ready-task dispatcher client

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.client.dispatcher import DispatcherWorker, DispatchOutbox
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.ready_dispatch import DispatchAssignment

PROJECT = "SYNAPSE-CHANNEL"


# --- outbox --------------------------------------------------------------------


def _intent(tmp_path: Path, **overrides: Any) -> DispatchOutbox:
    outbox = DispatchOutbox(tmp_path / "outbox.jsonl")
    assignment = DispatchAssignment(
        task_id="T-1",
        owner=f"{PROJECT}/kimi-3dcd",
        wake_identity=f"{PROJECT}/kimi-3dcd-rx",
        class_score=1,
        reasons=("matched",),
    )
    outbox.record_assignment(assignment, 3, now=100.0)
    return outbox


def test_outbox_record_and_pending_order(tmp_path: Path) -> None:
    outbox = _intent(tmp_path)
    pending = outbox.pending()
    assert len(pending) == 1
    assert pending[0].wake_id == f"T-1:{PROJECT}/kimi-3dcd:v3"
    assert pending[0].idem_key == f"dispatch-wake-T-1-{PROJECT}/kimi-3dcd-v3"


def test_outbox_persists_and_reloads_with_stable_idem_key(tmp_path: Path) -> None:
    first = _intent(tmp_path)
    original = first.pending()[0]
    reloaded = DispatchOutbox(tmp_path / "outbox.jsonl")
    restored = reloaded.pending()[0]
    assert restored.idem_key == original.idem_key
    assert restored.state == "pending"
    again = reloaded.record_assignment(
        DispatchAssignment("T-1", original.owner, original.wake_identity, 1, ("x",)),
        3,
        now=200.0,
    )
    assert again.idem_key == original.idem_key


def test_outbox_transition_appends_and_rejects_unknown_state(tmp_path: Path) -> None:
    outbox = _intent(tmp_path)
    intent = outbox.pending()[0]
    updated = outbox.transition(intent, attempts=1)
    assert updated.attempts == 1
    delivered = outbox.transition(updated, state="delivered")
    assert delivered.state == "delivered"
    assert outbox.pending() == []
    with pytest.raises(ValueError, match="unknown outbox state"):
        outbox.transition(intent, state="bogus")


def test_outbox_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "outbox.jsonl"
    row = json.dumps(
        {
            "wake_id": "w",
            "task_id": "T",
            "owner": "o",
            "wake_identity": "wr",
            "task_version": 1,
            "idem_key": "k",
            "attempts": 2,
            "state": "pending",
            "assigned_at": 1.0,
        }
    )
    path.write_text('{"wake_id": ""}\nnot json\n' + row + "\n")
    outbox = DispatchOutbox(path)
    assert len(outbox.pending()) == 1
    assert outbox.pending()[0].attempts == 2


# --- fake agent ------------------------------------------------------------------


class _FakeAgent:
    """Records verbs and feeds canned hub messages to the dispatcher."""

    def __init__(self, name: str, collect: Any, **_kwargs: Any) -> None:
        self.name = name
        self.collect = collect
        self.running = True
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.lease_ok = True
        self.ready = True
        self.cas_response = "ok"

    async def connect(self) -> None:
        return None

    async def wait_until_ready(self, timeout: float) -> bool:
        del timeout
        return self.ready

    async def claim(
        self, task_id: str, note: str = "", ttl_seconds: float | None = None, **_kw: Any
    ) -> None:
        self.calls.append(("claim", {"task_id": task_id, "ttl": ttl_seconds}))
        if self.lease_ok:
            await self.collect({"type": MessageType.CLAIM_GRANTED, "task_id": task_id})
        else:
            await self.collect({"type": MessageType.CLAIM_DENIED, "task_id": task_id})

    async def update_ledger_task(self, task_id: str, **kwargs: Any) -> None:
        self.calls.append(("update_ledger_task", {"task_id": task_id, **kwargs}))
        if self.cas_response == "ok":
            await self.collect(
                {
                    "type": MessageType.LEDGER_TASK_UPDATED,
                    "task": {
                        "task_id": task_id,
                        "suggested_owner": kwargs.get("suggested_owner", ""),
                        "version": 4,
                    },
                }
            )
        elif self.cas_response == "conflict":
            await self.collect(
                {
                    "type": MessageType.ERROR,
                    "payload": (f"Task '{task_id}' version conflict: expected v9, board has v10."),
                }
            )
        elif self.cas_response == "foreign":
            await self.collect(
                {
                    "type": MessageType.LEDGER_TASK_UPDATED,
                    "task": {
                        "task_id": "OTHER",
                        "suggested_owner": "PROJ/other",
                        "version": 2,
                    },
                }
            )
            await self.collect(
                {
                    "type": MessageType.ERROR,
                    "payload": (f"Task '{task_id}' version conflict: expected v9, board has v10."),
                }
            )

    async def send_message(self, msg_type: str, **kwargs: Any) -> None:
        self.calls.append(("send_message", {"msg_type": msg_type, **kwargs}))

    async def post_progress(self, task_id: str, text: str, **_kw: Any) -> None:
        self.calls.append(("post_progress", {"task_id": task_id, "text": text}))

    async def request_board(self) -> None:
        await self.collect(self.snapshots["board"])

    async def request_state(self) -> None:
        await self.collect(self.snapshots["state"])

    async def request_manifest(self) -> None:
        await self.collect(self.snapshots["manifest"])

    async def request_who(self) -> None:
        await self.collect(self.snapshots["who"])


def _snapshots(
    *,
    tasks: list[dict[str, Any]],
    ready: list[str],
    claims: dict[str, Any] | None = None,
    cards: list[dict[str, Any]] | None = None,
    online: list[str] | None = None,
    wake: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    return {
        "board": {"type": MessageType.BOARD_SNAPSHOT, "board": {"tasks": tasks, "ready": ready}},
        "state": {"type": MessageType.STATE_SNAPSHOT, "snapshot": {"claims": claims or {}}},
        "manifest": {"type": MessageType.MANIFEST_SNAPSHOT, "manifest": cards or []},
        "who": {
            "type": MessageType.WHO_SNAPSHOT,
            "online_agents": online or [],
            "wake_capabilities": wake or {},
        },
    }


def _task(
    task_id: str,
    *,
    project: str = PROJECT,
    status: str = "open",
    suggested_owner: str = "",
    updated_at: float = 100.0,
    version: int = 3,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "title": task_id,
        "status": status,
        "project": project,
        "suggested_owner": suggested_owner,
        "updated_at": updated_at,
        "version": version,
    }


def _worker(tmp_path: Path, agent: _FakeAgent, **overrides: Any) -> DispatcherWorker:
    def _factory(_name: str, collect: Any, **_kwargs: Any) -> _FakeAgent:
        agent.collect = collect
        return agent

    kwargs: dict[str, Any] = {
        "project": PROJECT,
        "once": True,
        "outbox_path": tmp_path / "outbox.jsonl",
        "agent_factory": _factory,
    }
    kwargs.update(overrides)
    return DispatcherWorker(**kwargs)


def _seed(agent: _FakeAgent, snapshots: dict[str, dict[str, Any]]) -> None:
    agent.snapshots = snapshots


def test_unreachable_hub_returns_one(tmp_path: Path) -> None:
    agent = _FakeAgent("n", lambda d: None)
    agent.ready = False
    worker = _worker(tmp_path, agent)
    import asyncio

    assert asyncio.run(worker.run()) == 1


def test_lease_denied_yields_three(tmp_path: Path) -> None:
    import asyncio

    agent = _FakeAgent("n", lambda d: None)
    agent.lease_ok = False
    _seed(agent, _snapshots(tasks=[], ready=[]))
    assert asyncio.run(_worker(tmp_path, agent).run()) == 3


def test_pass_cas_updates_suggestion_and_wakes_with_idem_key(tmp_path: Path) -> None:
    import asyncio

    agent = _FakeAgent("n", lambda d: None)
    _seed(
        agent,
        _snapshots(
            tasks=[_task("T-1")],
            ready=["T-1"],
            cards=[{"agent": f"{PROJECT}/kimi-3dcd", "task_classes": [], "skills": []}],
            online=[f"{PROJECT}/kimi-3dcd-rx"],
            wake={f"{PROJECT}/kimi-3dcd-rx": "pane_bridge"},
        ),
    )
    assert asyncio.run(_worker(tmp_path, agent).run()) == 0
    update = next(call for verb, call in agent.calls if verb == "update_ledger_task")
    assert update == {
        "task_id": "T-1",
        "suggested_owner": f"{PROJECT}/kimi-3dcd",
        "expected_version": 3,
    }
    wake = next(call for verb, call in agent.calls if verb == "send_message")
    assert wake["target"] == f"{PROJECT}/kimi-3dcd-rx"
    assert wake["idem_key"] == f"dispatch-wake-T-1-{PROJECT}/kimi-3dcd-v3"
    assert "DISPATCH T-1" in wake["payload"]


def test_dry_run_mutates_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import asyncio

    agent = _FakeAgent("n", lambda d: None)
    _seed(
        agent,
        _snapshots(
            tasks=[_task("T-1")],
            ready=["T-1"],
            cards=[{"agent": f"{PROJECT}/kimi-3dcd", "task_classes": [], "skills": []}],
            online=[f"{PROJECT}/kimi-3dcd-rx"],
            wake={f"{PROJECT}/kimi-3dcd-rx": "pane_bridge"},
        ),
    )
    assert asyncio.run(_worker(tmp_path, agent, dry_run=True).run()) == 0
    assert agent.calls == []
    assert "[dry-run] T-1" in capsys.readouterr().out


def test_reconcile_marks_claimed_intent_delivered(tmp_path: Path) -> None:
    import asyncio

    agent = _FakeAgent("n", lambda d: None)
    outbox_path = tmp_path / "outbox.jsonl"
    pre = DispatchOutbox(outbox_path)
    intent = pre.record_assignment(
        DispatchAssignment("T-1", f"{PROJECT}/kimi-3dcd", f"{PROJECT}/kimi-3dcd-rx", 1, ("r",)),
        3,
        now=1.0,
    )
    assert intent.idem_key
    _seed(
        agent,
        _snapshots(
            tasks=[_task("T-1")],
            ready=["T-1"],
            claims={"T-1": {"owner": f"{PROJECT}/kimi-3dcd"}},
        ),
    )
    assert asyncio.run(_worker(tmp_path, agent).run()) == 0
    assert DispatchOutbox(outbox_path).pending() == []


def test_reconcile_conflicts_reowned_task(tmp_path: Path) -> None:
    import asyncio

    agent = _FakeAgent("n", lambda d: None)
    outbox_path = tmp_path / "outbox.jsonl"
    pre = DispatchOutbox(outbox_path)
    pre.record_assignment(
        DispatchAssignment("T-1", f"{PROJECT}/kimi-3dcd", f"{PROJECT}/kimi-3dcd-rx", 1, ("r",)),
        3,
        now=1.0,
    )
    _seed(agent, _snapshots(tasks=[_task("T-1", suggested_owner="PROJ/other")], ready=["T-1"]))
    assert asyncio.run(_worker(tmp_path, agent).run()) == 0
    assert DispatchOutbox(outbox_path).pending() == []


def test_reconcile_retries_same_idem_key_until_attempt_cap(tmp_path: Path) -> None:
    import asyncio

    agent = _FakeAgent("n", lambda d: None)
    outbox_path = tmp_path / "outbox.jsonl"
    pre = DispatchOutbox(outbox_path)
    seeded = pre.record_assignment(
        DispatchAssignment("T-1", f"{PROJECT}/kimi-3dcd", f"{PROJECT}/kimi-3dcd-rx", 1, ("r",)),
        3,
        now=1.0,
    )
    _seed(
        agent,
        _snapshots(tasks=[_task("T-1", suggested_owner=f"{PROJECT}/kimi-3dcd")], ready=["T-1"]),
    )
    assert asyncio.run(_worker(tmp_path, agent).run()) == 0
    wakes = [call for verb, call in agent.calls if verb == "send_message"]
    assert len(wakes) == 1
    assert wakes[0]["idem_key"] == seeded.idem_key
    assert DispatchOutbox(outbox_path).pending()[0].attempts == 1


def test_reconcile_abandons_after_max_attempts_with_progress_note(tmp_path: Path) -> None:
    import asyncio

    agent = _FakeAgent("n", lambda d: None)
    outbox_path = tmp_path / "outbox.jsonl"
    pre = DispatchOutbox(outbox_path)
    intent = pre.record_assignment(
        DispatchAssignment("T-1", f"{PROJECT}/kimi-3dcd", f"{PROJECT}/kimi-3dcd-rx", 1, ("r",)),
        3,
        now=1.0,
    )
    for _ in range(3):
        intent = pre.transition(intent, attempts=intent.attempts + 1)
    _seed(
        agent,
        _snapshots(tasks=[_task("T-1", suggested_owner=f"{PROJECT}/kimi-3dcd")], ready=["T-1"]),
    )
    assert asyncio.run(_worker(tmp_path, agent).run()) == 0
    notes = [call for verb, call in agent.calls if verb == "post_progress"]
    assert notes and "abandoned" in notes[0]["text"]
    assert DispatchOutbox(outbox_path).pending() == []


def test_worker_verbs_stay_inside_the_hard_boundary(tmp_path: Path) -> None:
    import asyncio

    agent = _FakeAgent("n", lambda d: None)
    _seed(
        agent,
        _snapshots(
            tasks=[_task("T-1"), _task("T-2")],
            ready=["T-1", "T-2"],
            cards=[{"agent": f"{PROJECT}/kimi-3dcd", "task_classes": [], "skills": []}],
            online=[f"{PROJECT}/kimi-3dcd-rx"],
            wake={f"{PROJECT}/kimi-3dcd-rx": "pane_bridge"},
        ),
    )
    assert asyncio.run(_worker(tmp_path, agent, capacity=2).run()) == 0
    allowed = {"claim", "update_ledger_task", "send_message", "post_progress"}
    assert {verb for verb, _ in agent.calls} <= allowed
    chat_targets = {call["target"] for verb, call in agent.calls if verb == "send_message"}
    assert chat_targets == {f"{PROJECT}/kimi-3dcd-rx"}
    lease = next(call for verb, call in agent.calls if verb == "claim")
    assert lease["task_id"] == f"dispatch:{PROJECT}"


def test_worker_requires_exact_project(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exact --project"):
        DispatcherWorker(project=" ", outbox_path=tmp_path / "o.jsonl")


def test_outbox_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "outbox.jsonl"
    row = json.dumps(
        {
            "wake_id": "w",
            "task_id": "T",
            "owner": "o",
            "wake_identity": "wr",
            "task_version": 1,
            "idem_key": "k",
            "attempts": 0,
            "state": "pending",
            "assigned_at": 1.0,
        }
    )
    path.write_text("\n" + row + "\n")
    assert len(DispatchOutbox(path).pending()) == 1


def test_lease_without_response_yields(tmp_path: Path) -> None:
    import asyncio

    class _SilentClaimAgent(_FakeAgent):
        async def claim(
            self, task_id: str, note: str = "", ttl_seconds: float | None = None, **_kw: Any
        ) -> None:
            self.calls.append(("claim", {"task_id": task_id}))

    agent = _SilentClaimAgent("n", lambda d: None)
    _seed(agent, _snapshots(tasks=[], ready=[]))
    assert asyncio.run(_worker(tmp_path, agent, response_timeout=0.1).run()) == 3


def test_incomplete_snapshot_set_retries_next_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import asyncio

    class _PartialSnapshotsAgent(_FakeAgent):
        async def request_who(self) -> None:
            return None

    agent = _PartialSnapshotsAgent("n", lambda d: None)
    _seed(agent, _snapshots(tasks=[], ready=[]))
    assert asyncio.run(_worker(tmp_path, agent, response_timeout=0.1).run()) == 0
    assert "snapshot fetch incomplete" in capsys.readouterr().out


def test_daemon_loop_sleeps_between_passes_and_cancels_cleanly(tmp_path: Path) -> None:
    import asyncio

    agent = _FakeAgent("n", lambda d: None)
    _seed(agent, _snapshots(tasks=[], ready=[]))
    worker = _worker(tmp_path, agent, once=False, interval=0.02)

    async def _drive() -> None:
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.08)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_drive())
    assert agent.running is False


def test_execute_skips_a_non_pending_intent(tmp_path: Path) -> None:
    import asyncio

    agent = _FakeAgent("n", lambda d: None)
    outbox_path = tmp_path / "outbox.jsonl"
    pre = DispatchOutbox(outbox_path)
    intent = pre.record_assignment(
        DispatchAssignment("T-1", f"{PROJECT}/kimi-3dcd", f"{PROJECT}/kimi-3dcd-rx", 1, ("r",)),
        3,
        now=1.0,
    )
    pre.transition(intent, state="delivered")
    _seed(
        agent,
        _snapshots(
            tasks=[_task("T-1")],
            ready=["T-1"],
            cards=[{"agent": f"{PROJECT}/kimi-3dcd", "task_classes": [], "skills": []}],
            online=[f"{PROJECT}/kimi-3dcd-rx"],
            wake={f"{PROJECT}/kimi-3dcd-rx": "pane_bridge"},
        ),
    )
    assert asyncio.run(_worker(tmp_path, agent).run()) == 0
    assert not [call for verb, call in agent.calls if verb == "update_ledger_task"]


def test_cas_conflict_aborts_nudge_without_wake(tmp_path: Path) -> None:
    import asyncio

    agent = _FakeAgent("n", lambda d: None)
    agent.cas_response = "conflict"
    _seed(
        agent,
        _snapshots(
            tasks=[_task("T-1")],
            ready=["T-1"],
            cards=[{"agent": f"{PROJECT}/kimi-3dcd", "task_classes": [], "skills": []}],
            online=[f"{PROJECT}/kimi-3dcd-rx"],
            wake={f"{PROJECT}/kimi-3dcd-rx": "pane_bridge"},
        ),
    )
    assert asyncio.run(_worker(tmp_path, agent).run()) == 0
    assert not [call for verb, call in agent.calls if verb == "send_message"]
    notes = [call for verb, call in agent.calls if verb == "post_progress"]
    assert notes and "nudge aborted" in notes[0]["text"]
    outbox = DispatchOutbox(tmp_path / "outbox.jsonl")
    assert outbox.pending() == []
    states = [intent.state for intent in outbox._intents.values()]
    assert states == ["conflicted"]


def test_cas_success_wakes_after_confirmed_verdict(tmp_path: Path) -> None:
    import asyncio

    agent = _FakeAgent("n", lambda d: None)
    _seed(
        agent,
        _snapshots(
            tasks=[_task("T-1")],
            ready=["T-1"],
            cards=[{"agent": f"{PROJECT}/kimi-3dcd", "task_classes": [], "skills": []}],
            online=[f"{PROJECT}/kimi-3dcd-rx"],
            wake={f"{PROJECT}/kimi-3dcd-rx": "pane_bridge"},
        ),
    )
    assert asyncio.run(_worker(tmp_path, agent).run()) == 0
    wakes = [call for verb, call in agent.calls if verb == "send_message"]
    assert len(wakes) == 1
    assert wakes[0]["idem_key"].startswith("dispatch-wake-T-1-")
    notes = [call for verb, call in agent.calls if verb == "post_progress"]
    assert notes == []


def test_cas_foreign_update_then_conflict_aborts_nudge(tmp_path: Path) -> None:
    import asyncio

    agent = _FakeAgent("n", lambda d: None)
    agent.cas_response = "foreign"
    _seed(
        agent,
        _snapshots(
            tasks=[_task("T-1")],
            ready=["T-1"],
            cards=[{"agent": f"{PROJECT}/kimi-3dcd", "task_classes": [], "skills": []}],
            online=[f"{PROJECT}/kimi-3dcd-rx"],
            wake={f"{PROJECT}/kimi-3dcd-rx": "pane_bridge"},
        ),
    )
    assert asyncio.run(_worker(tmp_path, agent).run()) == 0
    assert not [call for verb, call in agent.calls if verb == "send_message"]
    states = [
        intent.state for intent in DispatchOutbox(tmp_path / "outbox.jsonl")._intents.values()
    ]
    assert states == ["conflicted"]


def test_cas_timeout_without_verdict_aborts_nudge(tmp_path: Path) -> None:
    import asyncio

    agent = _FakeAgent("n", lambda d: None)
    agent.cas_response = "silent"
    _seed(
        agent,
        _snapshots(
            tasks=[_task("T-1")],
            ready=["T-1"],
            cards=[{"agent": f"{PROJECT}/kimi-3dcd", "task_classes": [], "skills": []}],
            online=[f"{PROJECT}/kimi-3dcd-rx"],
            wake={f"{PROJECT}/kimi-3dcd-rx": "pane_bridge"},
        ),
    )
    assert asyncio.run(_worker(tmp_path, agent, response_timeout=0.1).run()) == 0
    assert not [call for verb, call in agent.calls if verb == "send_message"]
    notes = [call for verb, call in agent.calls if verb == "post_progress"]
    assert notes and "nudge aborted" in notes[0]["text"]
    states = [
        intent.state for intent in DispatchOutbox(tmp_path / "outbox.jsonl")._intents.values()
    ]
    assert states == ["conflicted"]
