# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — role-claim enforcement: hub gate and end-to-end heartbeat binding

from __future__ import annotations

import json
import logging

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.role_grants import RoleGrants

_GRANTS = RoleGrants({"proj/coordinator": frozenset({"proj/claude"})})


class TestPermittedRoleClaims:
    def test_enforcement_off_permits_every_declared_role(self) -> None:
        # Even with a store present, enforcement off leaves the open posture unchanged.
        hub = SynapseHub(role_grants=_GRANTS, require_role_claim=False)

        assert hub.permitted_role_claims("proj/evil", ("proj/coordinator",)) == (
            "proj/coordinator",
        )

    def test_enforcement_on_keeps_only_granted_roles(self) -> None:
        hub = SynapseHub(role_grants=_GRANTS, require_role_claim=True)

        assert hub.permitted_role_claims("proj/claude", ("proj/coordinator", "proj/reviewer")) == (
            "proj/coordinator",
        )

    def test_enforcement_on_denies_a_squatter(self) -> None:
        hub = SynapseHub(role_grants=_GRANTS, require_role_claim=True)

        assert hub.permitted_role_claims("proj/evil", ("proj/coordinator",)) == ()

    def test_enforcement_on_without_a_store_denies_every_claim(self) -> None:
        hub = SynapseHub(role_grants=None, require_role_claim=True)

        assert hub.permitted_role_claims("proj/claude", ("proj/coordinator",)) == ()

    def test_denied_claims_are_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        hub = SynapseHub(role_grants=_GRANTS, require_role_claim=True)

        with caplog.at_level(logging.WARNING, logger="synapse.hub"):
            hub.permitted_role_claims("proj/evil", ("proj/coordinator",))

        assert any(
            "role-claim denied for proj/evil" in record.getMessage() for record in caplog.records
        )

    def test_a_permitted_claim_logs_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        hub = SynapseHub(role_grants=_GRANTS, require_role_claim=True)

        with caplog.at_level(logging.WARNING, logger="synapse.hub"):
            hub.permitted_role_claims("proj/claude", ("proj/coordinator",))

        assert not [r for r in caplog.records if "role-claim denied" in r.getMessage()]


async def test_granted_role_binds_under_enforcement_end_to_end() -> None:
    # With enforcement on and a matching grant, the declared role binds and shows in /who.
    hub = SynapseHub(hub_id="rc", role_grants=_GRANTS, require_role_claim=True)
    async with running_hub(hub) as (_hub, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await ws.send(
                json.dumps(
                    {
                        "sender": "proj/claude",
                        "type": "heartbeat",
                        "target": "System",
                        "payload": "online",
                        "roles": ["proj/coordinator"],
                    }
                )
            )
            await ws.send(json.dumps({"sender": "proj/claude", "type": "who_request"}))
            who = await read_until_type(ws, "who_snapshot")

            assert who["agent_roles"]["proj/claude"] == ["proj/coordinator"]


async def test_squatter_role_is_not_bound_under_enforcement_end_to_end() -> None:
    # An identity with no grant declares a privileged role; enforcement drops it, so the
    # role never binds and /who shows no roles for the squatter.
    hub = SynapseHub(hub_id="rc", role_grants=_GRANTS, require_role_claim=True)
    async with running_hub(hub) as (_hub, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await ws.send(
                json.dumps(
                    {
                        "sender": "proj/evil",
                        "type": "heartbeat",
                        "target": "System",
                        "payload": "online",
                        "roles": ["proj/coordinator"],
                    }
                )
            )
            await ws.send(json.dumps({"sender": "proj/evil", "type": "who_request"}))
            who = await read_until_type(ws, "who_snapshot")

            assert "proj/evil" not in who["agent_roles"]


async def test_declared_role_binds_unchanged_with_enforcement_off_end_to_end() -> None:
    # The default open posture: no store, enforcement off — any declared role binds, so a
    # single-user dev hub is unaffected by the new gate.
    hub = SynapseHub(hub_id="rc")
    async with running_hub(hub) as (_hub, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await ws.send(
                json.dumps(
                    {
                        "sender": "proj/anyone",
                        "type": "heartbeat",
                        "target": "System",
                        "payload": "online",
                        "roles": ["proj/coordinator"],
                    }
                )
            )
            await ws.send(json.dumps({"sender": "proj/anyone", "type": "who_request"}))
            who = await read_until_type(ws, "who_snapshot")

            assert who["agent_roles"]["proj/anyone"] == ["proj/coordinator"]
