# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the pool owner answers a peer hub's reservation (F02)
"""Handle ``spend_request``: decide a peer hub's reservation, settlement or query.

Only the pool owner's hub answers, and it answers only a peer its
``--multihub-serving-policy`` authorises on this connection, by certificate or by an
identity-key grant. The verified sender id is the caller the ledger scopes every
reservation to. A peer the hub will not serve, and a hub with no spend ledger, get
exactly the uniform refusal an authorised but refused peer gets, so nothing reveals
whether a pool, a ledger or a grant exists. The ledger decides in its own
serialized SQLite transaction on a worker thread, and the answer goes back
privately. Nothing is broadcast or written to the replicated journal.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from synapse_channel.core.protocol import MessageType
from synapse_channel.core.spend_ledger import SpendLedgerError
from synapse_channel.core.spend_wire import (
    REFUSALS,
    SpendWireError,
    decode_spend_request,
    encode_spend_result,
)

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.handler_context import HandlerContext
    from synapse_channel.core.multihub_serving import MultiHubServingPolicy
    from synapse_channel.core.spend_ledger import SpendLedger

    class SpendContext(HandlerContext, Protocol):
        """Capabilities consumed by spend handlers and their callees."""

        @property
        def multihub_serving_policy(self) -> MultiHubServingPolicy | None:
            """Return the multihub serving policy used by this handler family."""
            ...

        @property
        def spend_ledger(self) -> SpendLedger | None:
            """Return the spend ledger used by this handler family."""
            ...


logger = logging.getLogger("synapse.hub")


async def handle_spend_request(
    hub: SpendContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Answer one peer spend request privately, or refuse it uniformly."""
    try:
        action, document = decode_spend_request(data)
    except SpendWireError:
        await hub.send_json(
            websocket,
            hub.system("Malformed spend request", msg_type=MessageType.ERROR, target=sender),
        )
        return
    result: dict[str, object] = dict(REFUSALS[action])
    ledger = hub.spend_ledger
    policy = hub.multihub_serving_policy
    if (
        ledger is None
        or policy is None
        or not policy.authorise(sender=sender, websocket=websocket).allowed
    ):
        logger.warning("spend request refused: peer %r is not served", sender)
    else:
        now = datetime.now(timezone.utc)
        try:
            if action == "reserve":
                result = await asyncio.to_thread(ledger.reserve, sender, document, now=now)
            elif action == "settle":
                result = await asyncio.to_thread(ledger.settle, sender, document, now=now)
            else:
                result = await asyncio.to_thread(ledger.query, sender, document)
        except SpendLedgerError as exc:
            logger.error("spend ledger unavailable for %s from %r: %s", action, sender, exc)
    await hub.send_json(
        websocket,
        hub.system(
            "Spend result",
            msg_type=MessageType.SPEND_RESULT,
            target=sender,
            **encode_spend_result(action, result),
        ),
    )
