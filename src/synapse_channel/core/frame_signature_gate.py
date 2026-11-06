# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — require a connection's frames to carry its identity signature
"""Decide whether a frame on an identity-proven connection may proceed.

With ``require_signed_frames`` a hub checks every frame that arrives on an
already registered connection before anything else happens to it, apart
from the per-host rate limit that reading a frame applies. Every frame needs
a valid frame signature, read requests and unknown types included: handling
any frame on a registered connection refreshes the sender's presence and
liveness and clears its dead-letter record, so no frame may pass unsigned.
The registration frame itself is proven by the identity gate and never
reaches this check.

The public key is looked up again for every frame: the operator bundle key
must still exist, be unrevoked, unexpired and name the sender; a pinned key
must still be the name's pin. As at registration, a key is not checked
against a project scope: the ``<project>/<agent>`` sender name it must list
already fixes the project. A key that no longer qualifies closes the
connection. So does a connection without a recorded key, and a connection
whose refused frames reach :data:`BAD_SIGNATURE_LIMIT` in total; refusals
are counted over the whole connection, not consecutively.

A connection that only listens sends no frame to be checked, so keys are
also re-checked without one: at once after every change to the enrolled
keys, and every :data:`RECHECK_INTERVAL_SECONDS` while the hub serves, which
catches a key that expired, so such a connection stays open at most about
that long after its key expires. A connection whose key no longer qualifies
is told why and closed. A connection that cannot be told or closed, because
it is already going away, never stops the re-check of the others or the
hub.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from synapse_channel.core.frame_signature import FrameSignatureResult, verify_session_frame
from synapse_channel.core.frame_signing import FrameSigner, FrameSigningSessions
from synapse_channel.core.hub_identity_gate import IDENTITY_BINDING_CLOSE_CODE
from synapse_channel.core.identity_pins import IdentityPinStore
from synapse_channel.core.message_auth import EventSignatureTrustBundle
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.release_confirmation import release_error_correlation

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

logger = logging.getLogger("synapse.hub")

BAD_SIGNATURE_LIMIT = 5
"""Refused frames after which a connection is closed."""

NO_IDENTITY = "frame_signature_no_identity"
"""Refusal for a connection that has no recorded signing key."""

KEY_UNTRUSTED = "frame_signature_key_untrusted"
"""Refusal for a connection whose key is no longer trusted."""

RECHECK_INTERVAL_SECONDS = 5.0
"""Seconds between re-checks of every connection's key while the hub serves."""


class GateVerdict(str, Enum):
    """What the hub does with a checked frame."""

    ADMIT = "admit"
    REFUSE = "refuse"
    CLOSE = "close"


@dataclass(frozen=True)
class GateOutcome:
    """A verdict and the stable reason sent to the client.

    Parameters
    ----------
    verdict : GateVerdict
        Admit the frame, refuse it, or refuse it and close the connection.
    reason : str
        ``valid`` or a ``frame_signature_*`` reason code.
    """

    verdict: GateVerdict
    reason: str


def bundle_key(
    bundle: EventSignatureTrustBundle | None, key_id: str, sender: str, now: float
) -> Ed25519PublicKey | None:
    """Return the operator key ``key_id`` while it may still sign for ``sender``."""
    key = None if bundle is None else bundle.keys.get(key_id)
    if (
        key is None
        or key.revoked
        or (key.expires_at is not None and key.expires_at < now)
        or sender not in key.senders
    ):
        return None
    return key.verifier()


def pin_key(pins: IdentityPinStore | None, key_id: str, sender: str) -> Ed25519PublicKey | None:
    """Return the key pinned to ``sender`` while it is still ``key_id``.

    The pin store accepts only a decodable 32-byte key, so a stored pin
    always yields a public key.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    pin = None if pins is None else pins.pinned(sender)
    if pin is None or pin.key_id != key_id:
        return None
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(pin.public_key))


class FrameSignatureGate:
    """Check frames on registered connections against their proving key.

    Parameters
    ----------
    sessions : FrameSigningSessions
        The hub's per-connection signing records.
    hub_id : str
        This hub's identifier, as named in every welcome.
    enabled : Callable[[], bool]
        Whether the hub requires signed frames now.
    bundle : Callable[[], EventSignatureTrustBundle | None]
        The hub's current identity trust bundle, enrolled keys included.
    pins : IdentityPinStore or None
        The hub's trust-on-first-use pins.
    send_json : Callable[[Any, dict[str, Any]], Awaitable[None]]
        Sends one frame to a connection.
    system : Callable[..., dict[str, Any]]
        Builds a hub system frame.
    close_socket : Callable[..., Awaitable[None]]
        Closes a connection with a code and a reason.
    count_refusal : Callable[[], None]
        Counts one refused frame or binding.
    clock : Callable[[], float], optional
        Wall-clock source for key expiry.
    """

    def __init__(
        self,
        *,
        sessions: FrameSigningSessions,
        hub_id: str,
        enabled: Callable[[], bool],
        bundle: Callable[[], EventSignatureTrustBundle | None],
        pins: IdentityPinStore | None,
        send_json: Callable[[Any, dict[str, Any]], Awaitable[None]],
        system: Callable[..., dict[str, Any]],
        close_socket: Callable[..., Awaitable[None]],
        count_refusal: Callable[[], None],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sessions = sessions
        self._hub_id = hub_id
        self._enabled = enabled
        self._bundle = bundle
        self._pins = pins
        self._send_json = send_json
        self._system = system
        self._close_socket = close_socket
        self._count_refusal = count_refusal
        self._clock = clock

    def welcome_fields(self, websocket: Any) -> dict[str, str]:
        """Return the welcome fields for a connection: its nonce, and the requirement."""
        fields = self._sessions.welcome_fields(websocket)
        if self._enabled():
            fields["frame_signatures"] = "required"
        return fields

    def _resolve_key(self, signer: FrameSigner) -> Ed25519PublicKey | None:
        """Look up a recorded signer's key afresh; ``None`` once it is no longer trusted."""
        if signer.source == "bundle":
            return bundle_key(self._bundle(), signer.key_id, signer.sender, self._clock())
        return pin_key(self._pins, signer.key_id, signer.sender)

    def _still_trusted(self, signer: FrameSigner) -> bool:
        """Return whether a signer's key still resolves; a failed lookup counts as no."""
        try:
            return self._resolve_key(signer) is not None
        except Exception:
            logger.warning("cannot look up the key of %s; closing it", signer.sender, exc_info=True)
            return False

    def check(self, websocket: Any, sender: str, frame: Mapping[str, Any]) -> GateOutcome:
        """Return what to do with ``frame``, received on ``websocket`` bound to ``sender``.

        Parameters
        ----------
        websocket : Any
            The registered connection.
        sender : str
            The name the connection is bound to.
        frame : Mapping[str, Any]
            The frame exactly as received.

        Returns
        -------
        GateOutcome
            The verdict and its reason.
        """
        signer = self._sessions.signer(websocket, sender)
        if signer is None:
            return GateOutcome(GateVerdict.CLOSE, NO_IDENTITY)
        public_key = self._resolve_key(signer)
        if public_key is None:
            return GateOutcome(GateVerdict.CLOSE, KEY_UNTRUSTED)
        result, seq = verify_session_frame(
            frame,
            public_key=public_key,
            key_id=signer.key_id,
            hub_id=self._hub_id,
            session_nonce=self._sessions.session_nonce(websocket),
            last_seq=signer.last_seq,
        )
        if result is FrameSignatureResult.VALID:
            signer.last_seq = seq
            return GateOutcome(GateVerdict.ADMIT, result.value)
        signer.failures += 1
        verdict = (
            GateVerdict.CLOSE if signer.failures >= BAD_SIGNATURE_LIMIT else GateVerdict.REFUSE
        )
        return GateOutcome(verdict, result.value)

    async def admit(self, websocket: Any, bound: str | None, frame: dict[str, Any]) -> bool:
        """Check a registered connection's frame before anything else runs on it.

        A connection that is not yet bound, or a hub that does not require
        signed frames, passes: registration is proven by the identity gate.
        The frame is checked exactly as received. A refusal sends an error
        naming the reason; a closing refusal also closes the connection.

        Returns
        -------
        bool
            Whether the frame may proceed.
        """
        if bound is None or not self._enabled():
            return True
        outcome = self.check(websocket, bound, frame)
        if outcome.verdict is GateVerdict.ADMIT:
            return True
        await self._refuse(websocket, bound, outcome.reason, release_error_correlation(frame))
        if outcome.verdict is GateVerdict.CLOSE:
            await self._close_socket(
                websocket, code=IDENTITY_BINDING_CLOSE_CODE, reason="frame signature refused"
            )
        return False

    async def recheck(self) -> int:
        """Close every connection whose recorded key is no longer trusted.

        Each such connection is told :data:`KEY_UNTRUSTED` and closed, and its
        record is dropped at once so it is not closed twice. A key whose lookup
        fails counts as untrusted. The connections
        are closed concurrently, and one that cannot be told or closed is
        logged without affecting the others. Does nothing while the hub does
        not require signed frames.

        Returns
        -------
        int
            The number of connections closed.
        """
        if not self._enabled():
            return 0
        untrusted = [
            (websocket, signer)
            for websocket, signer in self._sessions.signers()
            if not self._still_trusted(signer)
        ]
        for websocket, _signer in untrusted:
            self._sessions.forget(websocket)
        await asyncio.gather(
            *(self._close_untrusted(websocket, signer.sender) for websocket, signer in untrusted)
        )
        return len(untrusted)

    async def _close_untrusted(self, websocket: Any, sender: str) -> None:
        """Tell one connection its key is no longer trusted and close it, best effort."""
        try:
            await self._refuse(websocket, sender, KEY_UNTRUSTED)
        except Exception:
            logger.warning("cannot tell %s its key is no longer trusted", sender, exc_info=True)
        try:
            await self._close_socket(
                websocket, code=IDENTITY_BINDING_CLOSE_CODE, reason="identity key no longer trusted"
            )
        except Exception:
            logger.warning("cannot close the connection of %s", sender, exc_info=True)

    async def recheck_loop(self, interval: float = RECHECK_INTERVAL_SECONDS) -> None:
        """Re-check every connection's key until cancelled with the server.

        A failed re-check is logged and the next one runs; the loop never ends
        on its own, so it cannot stop the hub.
        """
        while True:
            await asyncio.sleep(interval)
            try:
                await self.recheck()
            except Exception:
                logger.exception("cannot re-check the keys of signing connections")

    async def require_key(self, sender: str, websocket: Any) -> bool:
        """Refuse to bind a connection that must sign frames but proved no key.

        Applies to every new binding while signed frames are required,
        including a peer hub proven by its certificate: such a connection has
        no identity key, cannot sign, and is refused.

        Returns
        -------
        bool
            Whether the connection may bind.
        """
        if not self._enabled() or self._sessions.signer(websocket, sender) is not None:
            return True
        await self._refuse(websocket, sender, NO_IDENTITY)
        await self._close_socket(
            websocket, code=IDENTITY_BINDING_CLOSE_CODE, reason="identity proof required"
        )
        return False

    async def _refuse(
        self,
        websocket: Any,
        target: str,
        reason: str,
        correlation: Mapping[str, str] | None = None,
    ) -> None:
        """Count a refusal and tell the connection why.

        A refused keyed release carries its operation id, as every other
        ingress refusal does, so the sender can tell a refusal from an
        unanswered request.
        """
        self._count_refusal()
        hint = (
            " (this hub requires signed frames; upgrade the client)"
            if reason == FrameSignatureResult.MISSING.value
            else ""
        )
        await self._send_json(
            websocket,
            self._system(
                f"frame signature refused: {reason}{hint}",
                msg_type=MessageType.ERROR,
                target=target,
                verification_result=reason,
                **(correlation or {}),
            ),
        )
