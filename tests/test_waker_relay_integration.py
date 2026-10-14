# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bound waker relay through real CLI and hub processes
"""Exercise the public relay with real journal, receiver and policy files."""

from __future__ import annotations

import asyncio
import json
import math
import os
import socket
from pathlib import Path

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel import wake_marks
from synapse_channel.core.event_row_mac import load_or_create_row_mac_key
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.wake_adapters import WakeTarget
from synapse_channel.wake_relay_service import relay_bound_wake
from synapse_channel.waker_config import WakerConfig, save_waker_config
from test_wake_adapter_harness_waiter import SEAT, _armed_waiter, _command, _message_seq, _send

SESSION = "01a0fcac-3407-71c3-9081-0bff345ef6c2"
HOST = socket.gethostname()
TARGET = WakeTarget(SEAT, SESSION, HOST)


def _policy(tmp_path: Path, **changes: object) -> Path:
    """Write actual operator input in an isolated owner-only binding file."""
    document: dict[str, object] = {
        "schema_version": 1,
        "seat": SEAT,
        "native_session": SESSION,
        "execution_host": HOST,
        "marks_dir": str(tmp_path / "marks"),
        "adapters": ["harness_waiter"],
        "journal_db": str(tmp_path / "hub.db"),
    }
    document.update(changes)
    path = tmp_path / "binding.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o600)
    return path


async def _relay_cli(
    tmp_path: Path, binding: Path, wake_id: int, **changes: str
) -> tuple[int, str, str]:
    """Invoke the product's actual parser, policy loader, factory and relay."""
    values = {
        "identity": SEAT,
        "native-session": SESSION,
        "execution-host": HOST,
        "wake-id": str(wake_id),
        "deadline": "0.2",
    }
    values.update(changes)
    arguments = ["waker", "relay", "--binding-file", str(binding)]
    for key, value in values.items():
        arguments.extend([f"--{key}", value])
    child = await _command(tmp_path, *arguments)
    try:
        stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=20)
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
    assert child.returncode is not None
    return child.returncode, stdout.decode(), stderr.decode()


async def test_bound_cli_observes_actual_delivery_and_deduplicates_after_process_restart(
    tmp_path: Path,
) -> None:
    """Two separate relay processes retain exactly one accepted transport mark."""
    binding = _policy(tmp_path)
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(hub_id="bound-relay", journal=store)) as (hub, uri):
        waiter = await _armed_waiter(tmp_path, hub, uri, SEAT)
        try:
            assert await _send(tmp_path, uri, SEAT, "actual bound wake") == 0
            assert await asyncio.wait_for(waiter.wait(), timeout=20) == 0
            wake_id = _message_seq(tmp_path / "hub.db", "actual bound wake")
            first = await _relay_cli(tmp_path, binding, wake_id)
            second = await _relay_cli(tmp_path, binding, wake_id)
        finally:
            if waiter.returncode is None:
                waiter.kill()
                await waiter.wait()
    assert first[0] == second[0] == 0
    one, two = json.loads(first[1]), json.loads(second[1])
    assert one["state"] == two["state"] == "accepted"
    assert one["proves"] == "written_to_passive_receiver"
    assert one["sent_now"] and not two["sent_now"]
    assert one["attempts"] == two["attempts"]
    assert len(one["attempts"]) == 1


async def test_bound_cli_records_uncertainty_for_actual_held_mailbox_and_does_not_retry(
    tmp_path: Path,
) -> None:
    """A later receiver ACK does not reopen an already uncertain wake attempt."""
    binding = _policy(tmp_path)
    async with running_hub(
        SynapseHub(hub_id="bound-relay", journal=EventStore(tmp_path / "hub.db"))
    ) as (_, uri):
        assert await _send(tmp_path, uri, SEAT, "held bound wake") == 1
        wake_id = _message_seq(tmp_path / "hub.db", "held bound wake")
        first = await _relay_cli(tmp_path, binding, wake_id)
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
            assert await asyncio.wait_for(late.wait(), timeout=20) == 0
        finally:
            if late.returncode is None:
                late.kill()
                await late.wait()
        second = await _relay_cli(tmp_path, binding, wake_id)
    assert first[0] == second[0] == 1
    one, two = json.loads(first[1]), json.loads(second[1])
    assert one["state"] == two["state"] == "uncertain"
    assert one["sent_now"] and not two["sent_now"]
    assert one["attempts"] == two["attempts"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("identity", "OTHER/seat"),
        ("native-session", "00000000-0000-4000-8000-000000000000"),
        ("execution-host", "another-host"),
        ("deadline", "nan"),
        ("deadline", "inf"),
        ("deadline", "-1"),
        ("wake-id", "0"),
    ],
)
async def test_cli_mismatch_refuses_before_creating_marks_or_receipt_database(
    tmp_path: Path, field: str, value: str
) -> None:
    """Incorrect binding/cursor/timeouts cannot select an alternative session."""
    binding = _policy(tmp_path)
    result = await _relay_cli(tmp_path, binding, 1, **{field: value})
    assert result[0] == 2 and result[1] == ""
    assert "ValueError" in result[2]
    assert not (tmp_path / "marks").exists()
    assert not (tmp_path / "hub.db").exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"seat": "*"},
        {"native_session": "latest"},
        {"execution_host": "\x1bSECRET"},
        {"marks_dir": "relative"},
        {"marks_dir": None},
        {"adapters": []},
        {"adapters": [42]},
        {"adapters": ["missing"]},
        {"adapters": ["harness_waiter", "harness_waiter"]},
        {"adapters": ["native_queue"], "codex_bin": None},
        {"journal_db": None},
        {"journal_db": None, "key_file": str(Path(__file__).resolve().parent / "absent-key")},
        {
            "journal_db": None,
            "row_mac_key_file": str(Path(__file__).resolve().parent / "absent-key"),
        },
        {"unknown_key": "SECRET"},
        {"adapters": ["native_queue"], "codex_bin": "relative"},
    ],
)
async def test_real_policy_files_reject_ambiguous_or_unsafe_bindings_without_echo(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    """Policy parsing accepts no coercion or unspecified command transport."""
    result = await _relay_cli(tmp_path, _policy(tmp_path, **changes), 1)
    assert result[0] == 2
    assert "SECRET" not in result[1] + result[2]
    assert not (tmp_path / "marks").exists()


async def test_binding_to_another_host_is_not_a_remote_execution_route(tmp_path: Path) -> None:
    """Even a matching caller/profile cannot execute on a falsely selected host."""
    binding = _policy(tmp_path, execution_host="another-host")
    result = await _relay_cli(tmp_path, binding, 1, **{"execution-host": "another-host"})
    assert result[0] == 2 and not (tmp_path / "marks").exists()


@pytest.mark.parametrize("content", ["{", "[]", '{"schema_version": 1}'])
async def test_malformed_real_policy_files_are_refused(tmp_path: Path, content: str) -> None:
    """Malformed operator JSON reaches the real secure loader and parser."""
    binding = _policy(tmp_path)
    binding.write_text(content, encoding="utf-8")
    result = await _relay_cli(tmp_path, binding, 1)
    assert result[0] == 2 and not (tmp_path / "marks").exists()


def test_service_rejects_boolean_and_non_finite_controls_before_reading_a_policy(
    tmp_path: Path,
) -> None:
    """The public Python call shares the CLI's no-effect guard."""
    for value in (True, 0, -1):
        with pytest.raises(ValueError):
            relay_bound_wake(tmp_path / "absent", TARGET, value)
    for seconds in (True, -1.0, math.inf, math.nan):
        with pytest.raises(ValueError):
            relay_bound_wake(tmp_path / "absent", TARGET, 1, deadline_seconds=seconds)
    assert list(tmp_path.iterdir()) == []


async def test_nonexistent_native_executable_is_passed_over_without_attempt(tmp_path: Path) -> None:
    """The factory uses the actual fixed-path adapter and preserves unavailability."""
    binding = _policy(tmp_path, adapters=["native_queue"], codex_bin=str(tmp_path / "absent"))
    result = await _relay_cli(tmp_path, binding, 1)
    assert result[0] == 1
    receipt = json.loads(result[1])
    assert receipt["state"] == "wake_failed" and receipt["attempts"] == []
    assert receipt["passed_over"][0][0] == "native_queue"


async def test_multilink_policy_is_refused_without_any_transport_effect(tmp_path: Path) -> None:
    """The executable policy uses the same secure loader as waker configuration."""
    binding = _policy(tmp_path)
    os.link(binding, tmp_path / "alias.json")
    result = await _relay_cli(tmp_path, binding, 1)
    assert result[0] == 2 and "SecretFileError" in result[2]
    assert not (tmp_path / "marks").exists()


@pytest.mark.parametrize("desired", ["armed", "inhibited"])
async def test_pane_factory_reads_actual_lifecycle_binding_without_starting_a_provider(
    tmp_path: Path, desired: str
) -> None:
    """Real durable control is honoured before the pane transport can be touched."""
    home = tmp_path / "home"
    home.mkdir()
    config = WakerConfig(
        identity=SEAT,
        session="missing-owned-pane",
        cwd=str(tmp_path),
        agent_command=("codex",),
        registry_dir=str(tmp_path / "pane-registry"),
        tmux_bin=str(tmp_path / "missing-tmux"),
        desired_state=desired,
    )
    save_waker_config(config, home=home)
    binding = _policy(tmp_path, adapters=["pane_bridge"])
    result = await _relay_cli(tmp_path, binding, 1)
    assert result[0] == 2
    assert not (tmp_path / "pane-registry").exists()
    assert wake_marks.read(tmp_path / "marks", SEAT, 1) == ()


@pytest.mark.parametrize("key_available", [True, False])
async def test_authenticated_live_journal_uses_existing_row_key_or_records_uncertainty(
    tmp_path: Path, key_available: bool
) -> None:
    """Real MAC-protected receipts are read without provisioning a missing key."""
    key_path = tmp_path / "row.key"
    row_key = load_or_create_row_mac_key(key_path, current_max_seq=0, log_has_macs=False)
    store = EventStore(tmp_path / "hub.db")
    store.enable_row_mac(row_key)
    selected_key = key_path if key_available else tmp_path / "missing-row.key"
    binding = _policy(tmp_path, row_mac_key_file=str(selected_key))
    async with running_hub(SynapseHub(hub_id="authenticated-relay", journal=store)) as (hub, uri):
        waiter = await _armed_waiter(tmp_path, hub, uri, SEAT)
        try:
            assert await _send(tmp_path, uri, SEAT, "authenticated wake") == 0
            assert await asyncio.wait_for(waiter.wait(), timeout=20) == 0
            seq = next(
                event.seq
                for event in store.iter_events(kinds=(EventKind.CHAT,))
                if event.payload.get("payload") == "authenticated wake"
            )
            assert store.has_row_macs()
            result = await _relay_cli(tmp_path, binding, seq)
        finally:
            if waiter.returncode is None:
                waiter.kill()
                await waiter.wait()
    assert result[0] == (0 if key_available else 1)
    receipt = json.loads(result[1])
    assert receipt["state"] == ("accepted" if key_available else "uncertain")
    assert receipt["proves"] == ("written_to_passive_receiver" if key_available else "")
    assert not (tmp_path / "missing-row.key").exists()
