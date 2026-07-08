# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — admit a socket only when it proves the identity it registers as
"""Connection-identity admission gate for the routing hub.

:class:`HubIdentityGate` runs one check at the moment a socket first binds a name:
when connection-identity binding is required, the registering frame must carry a
valid identity signature verified against the operator-managed identity trust bundle
(:mod:`synapse_channel.core.identity_binding`). It resolves the connection credential
to the claimed audit subject before the hub trusts that subject for the ``-rx``
mailbox gate or a role claim; a socket that cannot prove its identity is refused and
closed, fail-closed.

With binding off — the default open/loopback posture — the gate admits every socket
unchanged, so a single-user dev hub needs no keys. The gate carries no back-reference
to the hub: like :class:`~synapse_channel.core.hub_frame_gates.HubFrameGates` it takes
the hub's per-socket send and system-message factory as injected callbacks, and its
denials are logged through a logger named ``synapse.hub`` so their records stay under
the hub's log namespace.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.core.identity_binding import verify_registration
from synapse_channel.core.message_auth import (
    EventSignatureTrustBundle,
    SignedEventVerificationResult,
)
from synapse_channel.core.protocol import MessageType

logger = logging.getLogger("synapse.hub")

IDENTITY_BINDING_CLOSE_CODE = 4013
"""WebSocket close code for a socket refused because it did not prove its identity."""


class HubIdentityGate:
    """Admit a socket only when it proves the identity it registers as.

    Parameters
    ----------
    require_identity_binding : bool
        When ``True``, a socket's first (name-binding) frame must carry a valid
        identity signature or the socket is refused and closed. When ``False`` the
        gate admits every socket, so an open hub is unchanged.
    identity_trust_bundle : EventSignatureTrustBundle or None
        Operator-managed identity keys (separate material from federation and
        signed-event trust). With binding required but no bundle configured the gate
        fails closed — it can verify nobody, so it admits nobody.
    send_json : Callable[[Any, dict], Awaitable[None]]
        The hub's per-socket send (``hub._send_json``), used to deliver the denial.
    system : Callable[..., dict]
        The hub's system-message factory (``hub._system``), used to stamp the denial
        with the hub id.
    clock : Callable[[], float], optional
        Wall-clock source for signature freshness; defaults to :func:`time.time`
        (the signed-registration timestamp is wall-clock, not the hub's monotonic
        uptime clock).
    """

    def __init__(
        self,
        *,
        require_identity_binding: bool,
        identity_trust_bundle: EventSignatureTrustBundle | None,
        send_json: Callable[[Any, dict[str, Any]], Awaitable[None]],
        system: Callable[..., dict[str, Any]],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._require_identity_binding = require_identity_binding
        self._identity_trust_bundle = identity_trust_bundle
        self._send_json = send_json
        self._system = system
        self._clock = clock

    async def verify_identity(self, sender: str, data: dict[str, Any], websocket: Any) -> bool:
        """Verify a first frame's identity signature; refuse and close on failure.

        Returns ``True`` when binding is off, or the frame proves ``sender`` against
        the trust bundle. A frame that fails — missing signature, unknown or revoked
        key, sender mismatch, expiry, replay, or a required-but-absent bundle — is
        refused: an error naming the reason is sent and the socket is closed
        (:data:`IDENTITY_BINDING_CLOSE_CODE`), and ``False`` is returned so the hub
        stops routing it before the name is bound.
        """
        if not self._require_identity_binding:
            return True
        bundle = self._identity_trust_bundle
        if bundle is None:
            result = SignedEventVerificationResult.UNKNOWN_KEY
        else:
            result = verify_registration(
                data, trust_bundle=bundle, now=self._clock(), required_sender=sender
            )
        if result is SignedEventVerificationResult.VALID:
            return True
        logger.warning("identity binding denied for %s: %s", sender, result.value)
        await self._send_json(
            websocket,
            self._system(
                f"identity binding failed: {result.value}",
                msg_type=MessageType.ERROR,
                target=sender,
                verification_result=result.value,
            ),
        )
        await websocket.close(code=IDENTITY_BINDING_CLOSE_CODE, reason="identity binding failed")
        return False
