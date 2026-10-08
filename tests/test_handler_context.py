# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — structural handler boundary and late-binding acceptance
"""Qualify the static capability boundary and its real late-bound dispatch path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mypy import api as mypy_api

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore

ROOT = Path(__file__).resolve().parents[1]


def _check_contract(tmp_path: Path, source: str) -> tuple[str, str, int]:
    """Check an independent consumer with the actual repository strict profile."""
    fixture = tmp_path / "handler_consumer.py"
    fixture.write_text(source, encoding="utf-8")
    return mypy_api.run(
        [
            "--strict",
            "--config-file",
            str(ROOT / "pyproject.toml"),
            "--cache-dir",
            str(tmp_path / "mypy"),
            "--no-error-summary",
            str(fixture),
        ]
    )


def test_concrete_dispatch_and_candidate_claim_context_are_assignable(tmp_path: Path) -> None:
    """Real handlers fit concrete dispatch while the candidate keeps its narrow seam."""
    stdout, stderr, status = _check_contract(
        tmp_path,
        """\
from synapse_channel.core.handlers import DISPATCH, Handler
from synapse_channel.core.handlers.leasing import LeasingContext, apply_claim
from synapse_channel.core.handlers.message_forward import MessageForwardContext
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import SynapseState

def accept(hub: SynapseHub) -> None:
    lease: LeasingContext = hub
    forwarding: MessageForwardContext = hub
    handler: Handler = DISPATCH["claim"]
    _ = lease, forwarding, handler

class Candidate:
    state = SynapseState()
    journal: EventStore | None = None
    waits: dict[str, set[str]] = {}

result = apply_claim(Candidate(), claimant="owner", body={"task_id": "consumer"})
""",
    )
    assert status == 0, stdout + stderr


@pytest.mark.parametrize(
    ("body", "diagnostic"),
    [
        (
            "hub.channels = hub.channels",
            'Property "channels" defined in "ChannelsContext" is read-only',
        ),
        ("hub.spend_ledger", '"ChannelsContext" has no attribute "spend_ledger"'),
        ("await hub.send_json(None, [])", 'incompatible type "list[Never]"'),
        ("hub.system(42)", 'incompatible type "int"'),
        ("hub.max_history + 'invalid'", 'Unsupported operand types for + ("int" and "str")'),
    ],
)
def test_family_capabilities_refuse_invalid_consumers(
    tmp_path: Path, body: str, diagnostic: str
) -> None:
    """Capability boundaries reject replacement, unrelated authority and wrong wire shapes."""
    stdout, stderr, status = _check_contract(
        tmp_path,
        "from synapse_channel.core.handlers.channels import ChannelsContext\n"
        "async def use(hub: ChannelsContext) -> None:\n"
        f"    {body}\n",
    )
    assert status == 1, stdout + stderr
    assert diagnostic in stdout
    assert stderr == ""


async def test_live_claim_dispatch_preserves_late_bound_transport_and_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacing methods after composition still affects real durable claim dispatch."""
    journal = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="handler-context", journal=journal)
    original_system = hub.system
    original_broadcast = hub.broadcast
    emitted: list[str] = []

    def stamped(payload: str, **extra: Any) -> dict[str, Any]:
        """Stamp the real system response without replacing its transport."""
        return original_system(payload, context_stamp="late-bound", **extra)

    async def observed(data: dict[str, Any]) -> frozenset[str]:
        """Observe the real fan-out selected by the handler at call time."""
        emitted.append(str(data.get("type")))
        return await original_broadcast(data)

    async with running_hub(hub) as (_, uri):
        assert hub.bound_address == ("127.0.0.1", int(uri.rsplit(":", 1)[1]))
        owner = await connect_agent("context-owner", uri)
        observer = await connect_agent("context-observer", uri)
        try:
            monkeypatch.setattr(hub, "system", stamped)
            monkeypatch.setattr(hub, "broadcast", observed)
            await owner.agent.claim("context-task")
            granted = await observer.recorder.wait_for(
                lambda frame: frame.get("type") == "claim_granted"
            )
            assert granted["context_stamp"] == "late-bound"
            assert granted["owner"] == "context-owner"
            assert granted["hub_id"] == "handler-context"
            assert "claim_granted" in emitted
            assert hub.state.claims["context-task"].owner == "context-owner"
            owner_events = journal.read_since(0)
            assert any(
                event.kind == "claim" and event.payload["task_id"] == "context-task"
                for event in owner_events
            )
        finally:
            await close_agents(owner, observer)
    journal.close()
