# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — supervised lease owning-hub admission
"""A relay cannot qualify another hub's renewal support or restart session."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from cli_e2e_helpers import run_cli
from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.multihub_claim_transport import ClaimForwardPeer
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.protocol import MessageType


async def test_supervised_command_requires_owning_hub_before_forwarding(tmp_path: Path) -> None:
    owner = SynapseHub(
        hub_id="owner",
        namespace_ownership=NamespaceOwnership(
            owners={"project": "owner"},
            local_hub_id="owner",
        ),
    )
    marker = tmp_path / "command.started"
    async with running_hub(owner) as (_, owner_uri):
        edge = SynapseHub(
            hub_id="relay",
            namespace_ownership=NamespaceOwnership(
                owners={"project": "owner"},
                local_hub_id="relay",
            ),
            claim_peers={"owner": ClaimForwardPeer(uri=owner_uri)},
        )
        async with running_hub(edge) as (_, edge_uri):
            arguments = (
                "lock",
                "mutex",
                "--name",
                "project/actor",
                "--wait-timeout",
                "0",
                "--",
                sys.executable,
                "-c",
                "from pathlib import Path;import sys;Path(sys.argv[1]).touch()",
                str(marker),
            )
            refused = await asyncio.to_thread(run_cli, *arguments, uri=edge_uri)
            assert refused.returncode == 1, refused.output
            assert "direct connection to the owning hub" in refused.output
            assert not marker.exists()
            assert not owner.state.claims and not edge.state.claims
            assert edge.counters.forwarded_claims == 0
            agent = await connect_agent("project/actor", edge_uri)
            try:
                await agent.agent.send_message(
                    MessageType.CLAIM,
                    target="System",
                    payload="mutex",
                    task_id="mutex",
                    renew_epoch=1,
                    renewal_id="no-forward",
                )
                denial = await agent.recorder.wait_for(
                    lambda data: data.get("renewal_id") == "no-forward"
                )
                assert denial["reason_code"] == "RENEWAL_REQUIRES_OWNER_HUB"
                assert edge.counters.forwarded_claims == 0
            finally:
                await close_agents(agent)
            admitted = await asyncio.to_thread(run_cli, *arguments, uri=owner_uri)
            assert admitted.returncode == 0, admitted.output
            assert marker.exists()
            assert not owner.state.claims
