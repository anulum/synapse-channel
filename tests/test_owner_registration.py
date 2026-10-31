# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — public owner profile lifecycle acceptance
"""A ready public client owns its name; a contender cannot take it over."""

from __future__ import annotations

import secrets
from pathlib import Path

import pytest

from claim_outcome_helpers import ClaimProxy, claim_proxy
from hub_e2e_helpers import running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.owner_registration import close_owner, register_owner
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from test_cli_lock_supervision import renewal_proxy


@pytest.mark.parametrize("secured", [False, True])
async def test_callbackless_registration_confirms_binding_and_preserves_a_live_owner(
    secured: bool,
) -> None:
    token = secrets.token_urlsafe(32) if secured else None
    authenticator = TokenAuthenticator({token: ["library/owner"]}) if token else None
    async with running_hub(SynapseHub(authenticator=authenticator)) as (hub, uri):
        owner = SynapseAgent("library/owner", uri=uri, token=token, verbose=False)
        listener = await register_owner(owner, 2)
        try:
            assert listener is not None and not listener.done()
            assert owner.name in hub.clients.agent_sockets
            contender = SynapseAgent(owner.name, uri=uri, token=token, verbose=False)
            refused = await register_owner(contender, 0.3)
            await close_owner(contender, refused)
            assert refused is None
            assert owner.name in hub.clients.agent_sockets
            assert owner.running and not listener.done()
        finally:
            await close_owner(owner, listener)


@pytest.mark.parametrize("fault", ["drop", "target", "request_id", "hub_id"])
async def test_registration_refuses_missing_or_mismatched_binding_then_rebinds(
    fault: str,
) -> None:
    """A real wire fault blocks mutation and leaves the name available afterward."""

    def damage(data: dict[str, object]) -> None:
        if data.get("type") == "state_snapshot":
            data[fault] = "unrelated-binding"

    async with running_hub() as (hub, upstream):
        proxy = ClaimProxy(
            upstream,
            drop_grants=False,
            drop_snapshots=fault == "drop",
            transform=damage,
            preserve_registration=False,
        )
        async with claim_proxy(proxy) as uri:
            agent = SynapseAgent("binding/owner", uri=uri, verbose=False)
            # The refusal arrives with the damaged snapshot, so a full budget
            # costs nothing; a short one let a slow runner time out before the
            # state request was even sent.
            listener = await register_owner(agent, 2)
            assert listener is None and not agent.running
            assert proxy.requests.count("state_request") == 1
            assert "claim" not in proxy.requests and "release" not in proxy.requests
        recovered = SynapseAgent(agent.name, uri=upstream, verbose=False)
        listener = await register_owner(recovered, 2)
        try:
            assert listener is not None and not listener.done()
            assert recovered.name in hub.clients.agent_sockets
        finally:
            await close_owner(recovered, listener)


async def test_pending_retry_retains_the_last_observed_name_refusal(tmp_path: Path) -> None:
    """A real delayed retry cannot erase the preceding actual hub close reason."""
    retried = tmp_path / "retried"
    async with running_hub() as (hub, upstream):
        owner = SynapseAgent("diagnostic/owner", uri=upstream, verbose=False)
        listener = await register_owner(owner, 2)
        try:
            assert listener is not None
            async with renewal_proxy(upstream, "stall-reconnect", gate=retried) as uri:
                contender = SynapseAgent(owner.name, uri=uri, verbose=False)
                # The retry must happen inside the budget; a slow runner needs room.
                refused = await register_owner(contender, 2)
                assert refused is None and retried.exists()
                assert contender.last_close_code == 4009
                assert contender.last_close_reason == "name conflict"
                assert not contender.running
                assert owner.name in hub.clients.agent_sockets
                assert owner.running and not listener.done()
        finally:
            await close_owner(owner, listener)
