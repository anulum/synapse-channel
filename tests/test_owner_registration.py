# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — public owner profile lifecycle acceptance
"""A ready public client owns its name; a contender cannot take it over."""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from typing import Any, cast

import pytest
from websockets.exceptions import ConnectionClosed

from claim_outcome_helpers import ClaimProxy, claim_proxy
from hub_e2e_helpers import running_hub
from synapse_channel.client import owner_registration
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.owner_registration import close_owner, register_owner
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType
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
            # The budget must cover the hub close. A timeout also returns None
            # and leaves no close code, so the assertion is the close itself.
            refused = await register_owner(contender, 2)
            await close_owner(contender, refused)
            assert refused is None
            assert contender.last_close_code == 4009
            assert contender.last_close_reason == "name conflict"
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


async def test_cancelling_registration_outlasts_a_swallowed_cancellation() -> None:
    """A probe that ignores one cancellation, as wait_for can on 3.11, is cancelled again."""

    async def swallows_one_cancel() -> bool:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass  # The cancellation is lost here, as in asyncio.wait_for on Python 3.11.
        await asyncio.Event().wait()  # Waits for a reply that never comes.
        return True

    async def plain_listener() -> None:
        await asyncio.sleep(3600)

    async def failing_listener() -> None:
        raise ConnectionError("hub went away")

    agent = SynapseAgent("P/owner", None, uri="ws://127.0.0.1:9", verbose=False)
    for listener_body in (plain_listener, failing_listener):
        ready = asyncio.create_task(swallows_one_cancel())
        listener = asyncio.create_task(listener_body())
        await asyncio.sleep(0)
        unfinished = await asyncio.wait_for(
            owner_registration._cancel_registration(agent, listener, ready),
            timeout=owner_registration.CANCEL_RETRY_SECONDS * 5,
        )
        assert ready.cancelled()
        assert listener.done()
        assert unfinished is (listener_body is plain_listener)
        assert agent.running is False


class _ScriptedOwner:
    """A client whose readiness, sending and listening are scripted per test."""

    def __init__(
        self,
        *,
        ready: bool = True,
        confirmation: bool = False,
        send_error: BaseException | None = None,
        hang: bool = False,
        listener_steps: int | None = None,
    ) -> None:
        self.name = "P/owner"
        self.hub_id = "hub"
        self.callback: Any = None
        self.running = True
        self.last_close_code: int | None = None
        self.last_close_reason = ""
        self._ready = ready
        self._confirmation = confirmation
        self._send_error = send_error
        self._hang = hang
        self._listener_steps = listener_steps
        self._probe_refused = asyncio.Event()
        self.listener_finished = False

    async def connect(self) -> None:
        if self._listener_steps is None:
            while self.running:
                await asyncio.sleep(0.01)
            return
        # Outlive the refused probe by a few loop turns, then end by itself.
        await self._probe_refused.wait()
        for _ in range(self._listener_steps):
            await asyncio.sleep(0)
        self.listener_finished = True

    async def wait_until_ready(self, timeout: float) -> bool:
        if self._hang:
            await asyncio.Event().wait()
        if self._confirmation:
            await self.callback({"type": MessageType.WELCOME, "lock_renewal_session": "s"})
        if not self._ready:
            self._probe_refused.set()
        return self._ready

    async def send_message(self, _msg_type: str, **_fields: Any) -> None:
        assert self._send_error is not None
        raise self._send_error


def _as_agent(owner: _ScriptedOwner) -> SynapseAgent:
    return cast(SynapseAgent, owner)


async def test_binding_probe_reports_a_client_that_never_became_ready() -> None:
    owner = _ScriptedOwner(ready=False)

    assert await owner_registration._registered_profile(_as_agent(owner), 0.05) is False
    assert owner.callback is None


@pytest.mark.parametrize("error", [ConnectionClosed(None, None), OSError("broken pipe")])
async def test_binding_probe_reports_a_socket_lost_before_the_confirmation(
    error: BaseException,
) -> None:
    owner = _ScriptedOwner(confirmation=True, send_error=error)

    assert await owner_registration._registered_profile(_as_agent(owner), 1.0) is False
    assert owner.callback is None


async def test_registration_waits_for_a_live_listener_after_a_refused_probe() -> None:
    # The probe fails while the listener still runs. The attempt spends the rest
    # of its budget waiting for that listener instead of cancelling it at once,
    # so the listener, which ends by itself a few loop turns later, finishes.
    owner = _ScriptedOwner(ready=False, listener_steps=5)

    assert await asyncio.wait_for(register_owner(_as_agent(owner), 30), 10) is None
    assert owner.listener_finished is True
    assert owner.running is False
    assert owner.last_close_code is None


async def test_cancelled_registration_joins_its_tasks_and_propagates() -> None:
    owner = _ScriptedOwner(hang=True)
    registration = asyncio.create_task(register_owner(_as_agent(owner), 30))
    await asyncio.sleep(0.05)
    registration.cancel()

    with pytest.raises(asyncio.CancelledError):
        await registration
    assert owner.running is False
