# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for persistent capability registration

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.capability import CapabilityRegistry

# --- registry unit surface ----------------------------------------------------


def test_persistent_registration_survives_disconnect_drop() -> None:
    registry = CapabilityRegistry()
    registry.advertise_persistent("PROJ/seat-1", task_classes=["audit"], now=100.0)
    registry.forget("PROJ/seat-1")
    entry = registry.get_persistent("PROJ/seat-1")
    assert entry is not None
    assert entry.card.task_classes == ("audit",)
    assert entry.dispatchable is True


def test_forget_persistent_is_the_opt_out() -> None:
    registry = CapabilityRegistry()
    registry.advertise_persistent("PROJ/seat-1", now=100.0)
    registry.forget_persistent("PROJ/seat-1")
    assert registry.get_persistent("PROJ/seat-1") is None
    assert registry.manifest(now=100.0) == []


def test_persistent_card_expires_only_after_its_own_ttl() -> None:
    registry = CapabilityRegistry(ttl_seconds=10.0, persistent_ttl_seconds=100.0)
    registry.advertise("PROJ/seat-1", now=0.0)
    registry.advertise_persistent("PROJ/seat-1", now=0.0)
    manifest = registry.manifest(now=50.0)
    assert len(manifest) == 1
    assert manifest[0]["persistent"] is True
    assert registry.manifest(now=101.0) == []


def test_persistent_refresh_extends_the_window() -> None:
    registry = CapabilityRegistry(persistent_ttl_seconds=100.0)
    registry.advertise_persistent("PROJ/seat-1", now=0.0)
    registry.advertise_persistent("PROJ/seat-1", now=90.0)
    manifest = registry.manifest(now=150.0)
    assert len(manifest) == 1
    assert manifest[0]["advertised_at"] == 90.0


def test_signature_mapping_is_carried_into_the_card() -> None:
    registry = CapabilityRegistry()
    signature = {"key_id": "k1", "signature": "deadbeef"}
    live = registry.advertise("PROJ/seat-1", signature=signature, now=1.0)
    persistent = registry.advertise_persistent("PROJ/seat-1", signature=signature, now=1.0)
    assert live.signature == signature
    assert persistent.signature == signature


def test_manifest_merges_live_and_persistent_into_one_entry() -> None:
    registry = CapabilityRegistry()
    registry.advertise("PROJ/seat-1", description="live", now=10.0)
    registry.advertise_persistent(
        "PROJ/seat-1", description="registered", dispatchable=False, now=5.0
    )
    manifest = registry.manifest(now=10.0)
    assert len(manifest) == 1
    entry = manifest[0]
    assert entry["description"] == "live"
    assert entry["persistent"] is True
    assert entry["dispatchable"] is False


def test_manifest_persistent_only_carries_flags_and_stays_sorted() -> None:
    registry = CapabilityRegistry()
    registry.advertise_persistent("PROJ/seat-b", now=1.0)
    registry.advertise_persistent("PROJ/seat-a", dispatchable=False, now=1.0)
    registry.advertise("AAA/live", now=1.0)
    manifest = registry.manifest(now=1.0)
    assert [entry["agent"] for entry in manifest] == ["AAA/live", "PROJ/seat-a", "PROJ/seat-b"]
    assert "persistent" not in manifest[0]
    assert manifest[1]["dispatchable"] is False
    assert manifest[2]["dispatchable"] is True


# --- hub wire surface ----------------------------------------------------------


async def test_persistent_advertise_broadcasts_flags_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        seat = await connect_agent("PROJ/kimi-3dcd", uri)
        watcher = await connect_agent("WATCH", uri)
        try:
            await seat.agent.advertise(
                description="audit seat",
                task_classes=["audit", "review"],
                persist=True,
            )
            advertised = await watcher.recorder.wait_for(
                lambda m: m.get("type") == "capability_advertised"
            )
            assert advertised["card"]["persistent"] is True
            assert advertised["card"]["dispatchable"] is True
            assert hub.capabilities.get_persistent("PROJ/kimi-3dcd") is not None
        finally:
            await close_agents(seat, watcher)


async def test_persistent_advertise_survives_disconnect_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        seat = await connect_agent("PROJ/kimi-3dcd", uri)
        await seat.agent.advertise(task_classes=["audit"], persist=True)
        await seat.recorder.wait_for(lambda m: m.get("type") == "capability_advertised")
        await seat.close()
        assert hub.capabilities.get("PROJ/kimi-3dcd") is None
        assert hub.capabilities.get_persistent("PROJ/kimi-3dcd") is not None


async def test_persistent_advertise_from_bare_identity_fails_closed() -> None:
    async with running_hub() as (hub, uri):
        poster = await connect_agent("USER", uri)
        try:
            await poster.agent.advertise(task_classes=["audit"], persist=True)
            error = await poster.recorder.wait_for(lambda m: m.get("type") == "error")
            assert "project-scoped" in error["payload"]
            assert hub.capabilities.get_persistent("USER") is None
        finally:
            await close_agents(poster)


async def test_sidecar_registers_card_for_its_seat_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        waiter = await connect_agent("PROJ/kimi-3dcd-rx", uri)
        try:
            await waiter.agent.advertise(
                agent="PROJ/kimi-3dcd", task_classes=["audit"], persist=True
            )
            advertised = await waiter.recorder.wait_for(
                lambda m: m.get("type") == "capability_advertised"
            )
            assert advertised["agent"] == "PROJ/kimi-3dcd"
            assert hub.capabilities.get_persistent("PROJ/kimi-3dcd") is not None
            assert hub.capabilities.get_persistent("PROJ/kimi-3dcd-rx") is None
        finally:
            await close_agents(waiter)


async def test_agent_override_to_foreign_identity_fails_closed() -> None:
    async with running_hub() as (hub, uri):
        poster = await connect_agent("PROJ/codex-23696", uri)
        try:
            await poster.agent.advertise(
                agent="PROJ/kimi-3dcd", task_classes=["audit"], persist=True
            )
            error = await poster.recorder.wait_for(lambda m: m.get("type") == "error")
            assert "-rx sidecar" in error["payload"]
            assert hub.capabilities.get_persistent("PROJ/kimi-3dcd") is None
        finally:
            await close_agents(poster)


async def test_agent_override_to_blank_seat_identity_with_persist_fails_closed() -> None:
    async with running_hub() as (hub, uri):
        waiter = await connect_agent("PROJ/-rx", uri)
        try:
            await waiter.agent.advertise(agent="PROJ/", task_classes=["audit"], persist=True)
            error = await waiter.recorder.wait_for(lambda m: m.get("type") == "error")
            assert "project-scoped" in error["payload"]
            assert hub.capabilities.get_persistent("PROJ/") is None
        finally:
            await close_agents(waiter)


async def test_sidecar_registers_live_card_for_its_seat_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        waiter = await connect_agent("PROJ/kimi-3dcd-rx", uri)
        try:
            await waiter.agent.advertise(agent="PROJ/kimi-3dcd", task_classes=["audit"])
            advertised = await waiter.recorder.wait_for(
                lambda m: m.get("type") == "capability_advertised"
            )
            assert advertised["agent"] == "PROJ/kimi-3dcd"
            assert "persistent" not in advertised["card"]
            assert hub.capabilities.get("PROJ/kimi-3dcd") is not None
            assert hub.capabilities.get_persistent("PROJ/kimi-3dcd") is None
        finally:
            await close_agents(waiter)


async def _ignore_message(_data: dict[str, Any]) -> None:
    """Drop inbound frames in offline agent tests."""


def _capture_factory(sent: list[dict[str, Any]]) -> Callable[..., Awaitable[None]]:
    """Return a send_message-compatible coroutine that records envelopes."""

    async def _capture(
        _msg_type: str,
        *,
        target: str = "",
        payload: str = "",
        sign_identity: bool = False,
        **extra: Any,
    ) -> None:
        del target, payload, sign_identity
        sent.append(extra)

    return _capture


def _signed_agent(tmp_path: Path, name: str = "PROJ/seat-1") -> SynapseAgent:
    """Build an unsigned-connection agent holding a real card-signing key."""
    key = Ed25519PrivateKey.generate()
    pem = tmp_path / "card.pem"
    pem.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    pem.chmod(0o600)
    return SynapseAgent(
        name,
        _ignore_message,
        uri="ws://unused",
        verbose=False,
        capability_card_key_path=str(pem),
        capability_card_key_id="k1",
    )


async def test_signed_advertise_refuses_foreign_agent_override(
    tmp_path: Path,
) -> None:
    agent = _signed_agent(tmp_path)
    with pytest.raises(ValueError, match="own identity"):
        await agent.advertise(agent="PROJ/other", description="x")


async def test_signed_advertise_persist_and_dispatchable_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _signed_agent(tmp_path)
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(agent, "send_message", _capture_factory(sent))
    await agent.advertise(description="x", persist=True, dispatchable=False)
    assert sent, "advertise did not emit any envelope"
    envelope = sent[0]
    assert envelope["persist"] is True
    assert envelope["dispatchable"] is False
    assert envelope["signature"]["key_id"] == "k1"


async def test_signed_advertise_without_persist_omits_new_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _signed_agent(tmp_path)
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(agent, "send_message", _capture_factory(sent))
    await agent.advertise(description="x")
    assert sent, "advertise did not emit any envelope"
    assert "persist" not in sent[0]
    assert "dispatchable" not in sent[0]
    assert "agent" not in sent[0]


async def test_unsigned_advertise_full_field_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_path
    agent = SynapseAgent("PROJ/seat-1", _ignore_message, uri="ws://unused", verbose=False)
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(agent, "send_message", _capture_factory(sent))
    await agent.advertise(
        description="full",
        skills=["mypy"],
        task_classes=["audit"],
        model="kimi",
        contracts=[{"task_class": "audit", "input_schema": {"type": "object"}}],
        meta={"lane": "security"},
        manifest_digest="sha256:abc",
        persist=True,
        dispatchable=True,
        agent="PROJ/seat-1",
    )
    assert sent, "advertise did not emit any envelope"
    envelope = sent[0]
    assert envelope["description"] == "full"
    assert envelope["skills"] == ["mypy"]
    assert envelope["task_classes"] == ["audit"]
    assert envelope["model"] == "kimi"
    assert envelope["contracts"][0]["task_class"] == "audit"
    assert envelope["meta"] == {"lane": "security"}
    assert envelope["manifest_digest"] == "sha256:abc"
    assert envelope["persist"] is True
    assert envelope["dispatchable"] is True
    assert envelope["agent"] == "PROJ/seat-1"


async def test_non_boolean_persist_fails_closed() -> None:
    async with running_hub() as (hub, uri):
        poster = await connect_agent("PROJ/seat-1", uri)
        try:
            await poster.agent.send_message("advertise", persist="yes")
            error = await poster.recorder.wait_for(lambda m: m.get("type") == "error")
            assert "boolean" in error["payload"]
            assert hub.capabilities.get_persistent("PROJ/seat-1") is None
        finally:
            await close_agents(poster)


async def test_non_boolean_dispatchable_fails_closed() -> None:
    async with running_hub() as (hub, uri):
        poster = await connect_agent("PROJ/seat-1", uri)
        try:
            await poster.agent.send_message("advertise", persist=True, dispatchable="no")
            error = await poster.recorder.wait_for(lambda m: m.get("type") == "error")
            assert "boolean" in error["payload"]
            assert hub.capabilities.get_persistent("PROJ/seat-1") is None
        finally:
            await close_agents(poster)


async def test_dispatchable_false_is_stored_and_flagged_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        seat = await connect_agent("PROJ/seat-1", uri)
        try:
            await seat.agent.advertise(persist=True, dispatchable=False)
            advertised = await seat.recorder.wait_for(
                lambda m: m.get("type") == "capability_advertised"
            )
            assert advertised["card"]["dispatchable"] is False
            entry = hub.capabilities.get_persistent("PROJ/seat-1")
            assert entry is not None and entry.dispatchable is False
        finally:
            await close_agents(seat)


async def test_manifest_snapshot_merges_live_and_persistent_end_to_end() -> None:
    async with running_hub() as (_, uri):
        live = await connect_agent("PROJ/live-1", uri)
        seat = await connect_agent("PROJ/seat-1", uri)
        user = await connect_agent("USER", uri)
        try:
            await live.agent.advertise(task_classes=["chat"])
            await seat.agent.advertise(task_classes=["audit"], persist=True)
            await seat.recorder.wait_for(lambda m: m.get("type") == "capability_advertised")
            await user.agent.request_manifest()
            snapshot = await user.recorder.wait_for(lambda m: m.get("type") == "manifest_snapshot")
            agents = {card["agent"]: card for card in snapshot["manifest"]}
            assert "persistent" not in agents["PROJ/live-1"]
            assert agents["PROJ/seat-1"]["persistent"] is True
            assert agents["PROJ/seat-1"]["task_classes"] == ["audit"]
        finally:
            await close_agents(live, seat, user)
