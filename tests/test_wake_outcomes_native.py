# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native wake transport outcome journeys
"""Keep real wake transport receipts independent of model and business outcomes."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.wake_capability import WAKE_DIRECT
from synapse_channel.wake_adapters import WakeTarget
from synapse_channel.wake_adapters.harness_waiter import HarnessWaiterAdapter, journal_receipts
from synapse_channel.wake_relay import RelayResult, relay
from test_wake_adapter_harness_waiter import SEAT, _armed_waiter, _command, _message_seq, _send
from test_waker_relay_integration import TARGET, _policy, _relay_cli


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["accepted", "uncertain", "refused"])
async def test_actual_cli_reports_transport_truth_and_preserves_replay(
    tmp_path: Path, kind: str
) -> None:
    """Use real hub delivery, receivers, journal and separate relay processes."""
    binding = _policy(tmp_path)
    async with running_hub(
        SynapseHub(hub_id="outcome-wake", journal=EventStore(tmp_path / "hub.db"))
    ) as (hub, uri):
        waiter = None
        direct = None
        try:
            if kind == "accepted":
                waiter = await _armed_waiter(tmp_path, hub, uri, SEAT)
            elif kind == "refused":
                direct = await connect_agent(SEAT, uri, wake_capability=WAKE_DIRECT)
            assert await _send(tmp_path, uri, SEAT, "scoped wake fixture") == (
                1 if kind == "uncertain" else 0
            )
            if waiter is not None:
                assert await asyncio.wait_for(waiter.wait(), 5) == 0
            wake_id = _message_seq(tmp_path / "hub.db", "scoped wake fixture")
            first = await _relay_cli(tmp_path, binding, wake_id)
            if kind == "uncertain":
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
                try:
                    assert await asyncio.wait_for(late.wait(), 5) == 0
                finally:
                    if late.returncode is None:
                        late.kill()
                        await late.wait()
            again = await _relay_cli(tmp_path, binding, wake_id)
        finally:
            if waiter is not None and waiter.returncode is None:
                waiter.kill()
                await waiter.wait()
            if direct is not None:
                await close_agents(direct)
    expected_exit = 0 if kind == "accepted" else 1
    assert first[0] == again[0] == expected_exit
    one, two = json.loads(first[1]), json.loads(again[1])
    assert one["sent_now"] and not two["sent_now"]
    assert len(one["attempts"]) == 1
    assert one["attempts"] == two["attempts"]
    outcome = one["execution_outcome"]
    assert outcome == two["execution_outcome"]
    assert outcome["schema"] == "synapse.execution-outcome.v1"
    assert outcome["operation_id"] == one["operation_id"] == two["operation_id"]
    assert outcome["boundary"] == "wake_transport"
    assert (
        outcome["state"]
        == {
            "accepted": "confirmed_done",
            "uncertain": "unknown",
            "refused": "confirmed_failed",
        }[kind]
    )
    assert outcome["external_effect"] == "unknown"
    assert outcome["external_recovery"] == "inquire_or_operator"
    assert not outcome["automatic_retry"]
    assert not outcome["stop_requested"] and not outcome["stop_confirmed"]
    assert not outcome["effects_undone"]


def test_no_adapter_is_not_started_without_business_inference(tmp_path: Path) -> None:
    """The actual empty adapter set cannot prove whether the durable message was read."""
    result = relay(tmp_path / "marks", TARGET, 10, ())
    report = result.to_json()["execution_outcome"]
    assert isinstance(report, dict)
    assert report["state"] == "not_started"
    assert report["external_effect"] == "unknown"
    assert not result.sent_now and not result.attempts


def test_legacy_public_result_without_operation_binding_stays_unknown() -> None:
    """An older caller's accepted result cannot invent a bound execution receipt."""
    legacy = RelayResult(**json.loads('{"state":"accepted","proves":"typed_into_idle_composer"}'))
    report = legacy.to_json()["execution_outcome"]
    assert isinstance(report, dict)
    assert report["operation_id"] == "wake-unbound"
    assert report["state"] == report["external_effect"] == "unknown"
    assert report["observation"] == "wake_identity_unbound"
    assert not report["automatic_retry"]


def test_killed_real_effect_writer_stays_unknown_without_fallback(tmp_path: Path) -> None:
    """A native process dies after its real plugin effect, before outcome commit."""
    effects = tmp_path / "effect.jsonl"
    marks = tmp_path / "marks"
    script = (
        "import pathlib,sys,threading; "
        "from test_wake_relay import FileAdapter; "
        "from synapse_channel.wake_adapters import WakeTarget; "
        "from synapse_channel.wake_relay import relay; "
        "root=pathlib.Path(sys.argv[1]); "
        "relay(root/'marks',WakeTarget('OUTCOME/worker'),20,"
        "[FileAdapter('native_queue',root/'effect.jsonl',gate=threading.Event())])"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).resolve().parent), env.get("PYTHONPATH", "")]
    )
    child = subprocess.Popen([sys.executable, "-B", "-c", script, str(tmp_path)], env=env)
    try:
        deadline = time.monotonic() + 4
        while not effects.exists() or not effects.read_bytes().endswith(b"\n"):
            assert child.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        child.kill()
        child.wait(timeout=3)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)
    assert len(effects.read_text().splitlines()) == 1
    adapter = HarnessWaiterAdapter(journal_receipts(tmp_path / "absent.db"))
    first = relay(marks, WakeTarget("OUTCOME/worker"), 20, [adapter], deadline_seconds=0)
    again = relay(marks, WakeTarget("OUTCOME/worker"), 20, [adapter], deadline_seconds=0)
    assert first.execution_outcome.to_json() == again.execution_outcome.to_json()
    assert first.execution_outcome.state.value == "unknown"
    assert not first.sent_now and not again.sent_now
    assert len(first.attempts) == 1 and first.attempts[0].state == "attempt"
    assert not (tmp_path / "absent.db").exists()
    assert len(effects.read_text().splitlines()) == 1
