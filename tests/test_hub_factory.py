# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real composition, graph custody and runtime integration
"""Exercise the factory through actual hub operations and live sockets."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core import hub_component_lifetime as checkpoint_module
from synapse_channel.core import hub_factory
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_component_callbacks import HubComponentCallbacks
from synapse_channel.core.hub_components import HubComponents
from synapse_channel.core.hub_config import HubConfig, HubLimits, config_fingerprint
from synapse_channel.core.hub_factory import build_components, build_hub
from synapse_channel.core.hub_ledger_guard import HubLedgerGuard
from synapse_channel.core.journal import EventKind
from synapse_channel.core.merkle_checkpoint import MerkleCheckpointStore
from synapse_channel.core.persistence import EventStore


async def test_custom_graph_is_installed_once_and_routes_real_socket_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A substituted real ledger drives live delivery, persistence and counters."""
    captured: list[HubComponents] = []

    def compose(config: HubConfig, callbacks: HubComponentCallbacks) -> HubComponents:
        """Compose the whole real graph and supply a distinct real ledger."""
        graph = build_components(config, callbacks)
        ledger = HubLedgerGuard(
            max_findings_per_agent=config.limits.max_findings_per_agent,
            journal=config.journal,
            message_seq=41,
        )
        graph = replace(graph, state=replace(graph.state, ledger=ledger))
        captured.append(graph)
        return graph

    def refuse_recomposition(
        _config: HubConfig, _callbacks: HubComponentCallbacks
    ) -> HubComponents:
        """Fail if the receiving constructor tries to rebuild the injected graph."""
        raise AssertionError("injected graph was rebuilt")

    monkeypatch.setattr(hub_factory, "build_components", refuse_recomposition)
    with EventStore(tmp_path / "events.db") as journal:
        requested = HubConfig(journal=journal, hub_id="injected-live")
        hub = build_hub(requested, component_factory=compose)
        graph = captured[0]
        assert len(captured) == 1
        assert hub.clients is graph.clients.clients
        assert hub.counters is graph.clients.counters
        assert hub.state_mutations is graph.state.mutations
        assert hub.config_epoch == config_fingerprint(requested)
        async with running_hub(hub) as (_, uri):
            alpha = await connect_agent("FACTORY/alpha", uri)
            beta = await connect_agent("FACTORY/beta", uri)
            try:
                await alpha.agent.chat("composed delivery", target="all")
                delivered = await beta.recorder.wait_for(
                    lambda msg: (
                        msg.get("type") == "chat" and msg.get("payload") == "composed delivery"
                    )
                )
                assert delivered["msg_id"] == 42
                assert delivered["hub_id"] == "injected-live"
                assert hub.chat_history[-1]["payload"] == "composed delivery"
            finally:
                await close_agents(alpha, beta)
        assert journal.count() > 0
        assert graph.lifetime.live_checkpoint is not None
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            graph.lifetime.live_checkpoint.store.latest()


def test_factory_refuses_foreign_graph_without_closing_its_checkpoint(tmp_path: Path) -> None:
    """Refusing another target's graph must preserve its resources and operation."""
    with EventStore(tmp_path / "foreign.db") as journal:
        requested = HubConfig(journal=journal)
        owner = SynapseHub.__new__(SynapseHub)
        graph = build_components(requested, owner.component_callbacks())

        def foreign(_config: HubConfig, _callbacks: HubComponentCallbacks) -> HubComponents:
            """Return the actual graph bound to a different, still-owned target."""
            return graph

        try:
            with pytest.raises(ValueError, match="another target"):
                build_hub(requested, component_factory=foreign)
            assert graph.lifetime.live_checkpoint is not None
            assert graph.lifetime.live_checkpoint.store.latest() is not None
            SynapseHub.__init__(owner, graph)
            assert owner.next_msg_id() == 1
        finally:
            if graph.lifetime.live_checkpoint is not None:
                graph.lifetime.live_checkpoint.close()


def test_stale_configuration_refusal_closes_only_the_new_owned_checkpoint(tmp_path: Path) -> None:
    """A factory returning another request cannot leak its just-acquired resource."""
    captured: list[HubComponents] = []

    def stale(config: HubConfig, callbacks: HubComponentCallbacks) -> HubComponents:
        """Compose a real graph while replacing the requested record identity."""
        graph = build_components(replace(config), callbacks)
        captured.append(graph)
        return graph

    with EventStore(tmp_path / "events.db") as journal:
        with pytest.raises(ValueError, match="another configuration"):
            build_hub(HubConfig(journal=journal), component_factory=stale)
        live = captured[0].lifetime.live_checkpoint
        assert live is not None
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            live.store.latest()
        journal.append(EventKind.CHAT, {"sender": "caller", "payload": "still writable"})
        assert journal.count() == 1


@pytest.mark.parametrize("persistent", [False, True])
def test_embedding_installation_failure_closes_checkpoint_and_preserves_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, persistent: bool
) -> None:
    """A real embedding attribute refusal cannot leak the composed checkpoint."""
    opened: list[MerkleCheckpointStore] = []

    class ObservedCheckpoint(MerkleCheckpointStore):
        """Keep the actual opened database for an ownership check after refusal."""

        def __init__(self, path: Path) -> None:
            """Open a real checkpoint connection and retain its reference."""
            super().__init__(path)
            opened.append(self)

    class RefusingEmbeddingHub(SynapseHub):
        """An embedding that rejects an installed identity through its public hook."""

        def __setattr__(self, name: str, value: object) -> None:
            """Refuse the hub identity while allowing normal graph acquisition."""
            if name == "hub_id":
                raise RuntimeError("embedding identity refused")
            super().__setattr__(name, value)

    monkeypatch.setattr(checkpoint_module, "MerkleCheckpointStore", ObservedCheckpoint)
    with EventStore(tmp_path / "events.db" if persistent else ":memory:") as journal:
        with pytest.raises(RuntimeError, match="embedding identity refused"):
            RefusingEmbeddingHub(HubConfig(journal=journal))
        assert len(opened) == int(persistent)
        if persistent:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                opened[0].latest()
        journal.append(EventKind.CHAT, {"sender": "caller", "payload": "after install refusal"})
        assert journal.count() == 1


def test_receiving_constructor_rejects_legacy_options_and_second_installation() -> None:
    """Graph injection cannot blend configuration or overwrite a live graph."""
    target = SynapseHub.__new__(SynapseHub)
    graph = build_components(HubConfig(), target.component_callbacks())
    with pytest.raises(TypeError, match="cannot combine"):
        SynapseHub.__init__(target, graph, max_clients=8)
    SynapseHub.__init__(target, graph)
    assert target.max_clients == graph.clients.clients.max_clients
    with pytest.raises(ValueError, match="already installed"):
        SynapseHub.__init__(target, graph)
    assert target.next_msg_id() == 1


@pytest.mark.parametrize("interval", [0.0, -1.0, float("nan"), float("inf"), -float("inf")])
def test_invalid_checkpoint_interval_refuses_before_acquisition(
    tmp_path: Path, interval: float
) -> None:
    """Invalid scheduling cannot leave a checkpoint or close the caller's journal."""
    with EventStore(tmp_path / "events.db") as journal:
        with pytest.raises(ValueError, match="positive finite"):
            build_hub(HubConfig(journal=journal, checkpoint_interval=interval))
        assert not (tmp_path / "events.db.checkpoint.db").exists()
        journal.append(EventKind.CHAT, {"sender": "caller", "payload": "after refusal"})
        assert journal.count() == 1


def test_components_keep_registry_aliases_and_state_callbacks_live() -> None:
    """All legacy alias maps and deferred state sources refer to current objects."""
    captured: list[HubComponents] = []

    def capture(config: HubConfig, callbacks: HubComponentCallbacks) -> HubComponents:
        """Retain the actual graph to inspect its public dependency contract."""
        graph = build_components(config, callbacks)
        captured.append(graph)
        return graph

    hub = build_hub(HubConfig(limits=HubLimits(max_clients=3)), component_factory=capture)
    graph = captured[0]
    assert hub.connected_clients is graph.clients.clients.connected_clients
    assert hub.unauth_clients is graph.clients.clients.unauth_clients
    assert hub.agent_sockets is graph.clients.clients.agent_sockets
    assert hub.agent_roles is graph.clients.clients.agent_roles
    assert hub.socket_agent is graph.clients.clients.socket_agent
    assert graph.callbacks.claims() is hub.state.claims
    assert graph.callbacks.tasks() is hub.blackboard.tasks
    replacement = build_hub(HubConfig(default_ttl_seconds=12))
    hub.state = replacement.state
    hub.blackboard = replacement.blackboard
    assert graph.callbacks.claims() is replacement.state.claims
    assert graph.callbacks.tasks() is replacement.blackboard.tasks


async def test_closed_factory_hub_can_reopen_checkpoint_and_serve(tmp_path: Path) -> None:
    """Closing a constructed hub preserves the real serving restart contract."""
    with EventStore(tmp_path / "events.db") as journal:
        hub = build_hub(HubConfig(journal=journal, hub_id="reopened"))
        hub.close()
        async with running_hub(hub) as (_, uri):
            agent = await connect_agent("FACTORY/reopened", uri)
            observer = await connect_agent("FACTORY/observer", uri)
            try:
                await agent.agent.chat("after reopen", target="all")
                await observer.recorder.wait_for(
                    lambda msg: msg.get("type") == "chat" and msg.get("payload") == "after reopen"
                )
            finally:
                await close_agents(agent, observer)
        assert journal.count() > 0
        hub.close()
