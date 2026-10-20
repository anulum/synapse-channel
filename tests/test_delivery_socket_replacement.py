# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — recipient replay after actual socket replacement
"""Keep callbacks from retired sockets away from a replacement recipient."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.handlers.delivery_modes import deliver_pending_delivery_notifications
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from test_delivery_hub import _intent, _register


@pytest.mark.real_hub
@pytest.mark.parametrize("cancel_before_publication", [False, True])
async def test_retired_socket_callback_cannot_reoffer_a_replacements_work(
    tmp_path: Path, cancel_before_publication: bool
) -> None:
    """Fence stale callbacks without losing committed offers or cancellation notices."""
    with EventStore(tmp_path / "hub.db") as store:
        async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (hub, uri):
            previous, old_session = await _register(
                uri, "P/receiver", capabilities={"follow_up": "native"}
            )
            old_socket = hub.clients.agent_sockets["P/receiver"]
            await previous.close()
            deadline = asyncio.get_running_loop().time() + 3
            while "P/receiver" in hub.clients.agent_sockets:
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.01)
            receiver, session = await _register(
                uri, "P/receiver", capabilities={"follow_up": "native"}
            )
            sender, _ = await _register(uri, "P/author")
            try:
                assert old_session is not None and session is not None
                assert session["incarnation"] == old_session["incarnation"]
                current_socket = hub.clients.agent_sockets["P/receiver"]
                assert old_socket is not current_socket
                await sender.send(json.dumps(_intent(session["incarnation"])))
                queued = await read_until_type(sender, MessageType.DELIVERY_STATUS)
                original_offer = await read_until_type(receiver, MessageType.DELIVERY_OFFER)
                if cancel_before_publication:
                    mutation = json.dumps(
                        [queued["operation_key"], "cancel-1", "cancel_requested", {}],
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("ascii")
                    cancellation = store.delivery.request_cancel(
                        queued["operation_key"],
                        mutation_id="cancel-1",
                        mutation_digest=hashlib.sha256(mutation).hexdigest(),
                        actor="P/author",
                    )
                    assert cancellation.disposition == "inserted"
                before = tuple(store.iter_events())
                pending = store.delivery.pending_notifications("P/receiver")

                await deliver_pending_delivery_notifications(
                    hub, sender="P/receiver", websocket=old_socket
                )

                assert tuple(store.iter_events()) == before
                record = store.delivery.get(queued["operation_key"])
                assert record is not None and record.stage == "queued"
                assert not record.boundary_delivered and not record.explicitly_acknowledged
                assert record.cancel_requested is cancel_before_publication
                assert store.delivery.pending_notifications("P/receiver") == pending
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(receiver.recv(), timeout=0.1)
                await deliver_pending_delivery_notifications(
                    hub, sender="P/receiver", websocket=current_socket
                )
                if cancel_before_publication:
                    notice = await read_until_type(receiver, MessageType.DELIVERY_STATUS)
                    assert notice["operation_key"] == queued["operation_key"]
                    assert notice["cancel_requested"] and notice["stage"] == "queued"
                    assert not store.delivery.pending_notifications("P/receiver")
                replayed = await read_until_type(receiver, MessageType.DELIVERY_OFFER)
                assert replayed == original_offer
                assert tuple(store.iter_events()) == before
            finally:
                await sender.close()
                await receiver.close()
