# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — admit a socket only when it proves the identity it registers as
"""Connection-identity admission gate for the routing hub.

:class:`HubIdentityGate` runs one check at the moment a socket first binds a name,
in one of two postures:

- **Operator-managed binding** (``--require-identity-binding``): the registering
  frame must carry a valid identity signature verified against the operator's
  identity trust bundle (:mod:`synapse_channel.core.identity_binding`). A socket
  that cannot prove its identity is refused and closed, fail-closed. Unchanged.
- **Trust-on-first-use** (the default loopback posture): no operator input at
  all. A registration signed with the auto-provisioned machine key
  (:mod:`synapse_channel.machine_identity`) that also presents its public half
  is verified self-contained; on the first valid proof the hub *pins* the name
  to that key (:mod:`synapse_channel.core.identity_pins`). From then on the
  name binds only to a frame proving possession of the pinned key — a missing
  signature or a different key is refused with an actionable error. Names that
  never sign stay unpinned and keep classic first-come semantics, so a
  pre-TOFU client is never locked out.

The gate resolves the connection credential to the claimed audit subject before
the hub trusts that subject for the ``-rx`` mailbox gate or a role claim. It
carries no back-reference to the hub: like
:class:`~synapse_channel.core.hub_frame_gates.HubFrameGates` it takes the hub's
per-socket send and system-message factory as injected callbacks, and its
denials are logged through a logger named ``synapse.hub`` so their records stay
under the hub's log namespace.
"""

from __future__ import annotations

import base64
import binascii
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.core.identity_binding import (
    DEFAULT_IDENTITY_REPLAY_CAPACITY,
    verify_registration,
)
from synapse_channel.core.identity_pins import IdentityPinStore
from synapse_channel.core.message_auth import (
    DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS,
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageReplayCache,
    SignedEventVerificationResult,
    verify_event_signature,
)
from synapse_channel.core.protocol import MessageType
from synapse_channel.terminal_text import shell_command_arg, shell_long_option

logger = logging.getLogger("synapse.hub")

IDENTITY_BINDING_CLOSE_CODE = 4013
"""WebSocket close code for a socket refused because it did not prove its identity."""

_ED25519_RAW_PUBLIC_KEY_BYTES = 32


class HubIdentityGate:
    """Admit a socket only when it proves the identity it registers as.

    Parameters
    ----------
    require_identity_binding : bool
        When ``True``, a socket's first (name-binding) frame must carry a valid
        identity signature against the operator trust bundle or the socket is
        refused and closed; the trust-on-first-use posture is skipped entirely.
        When ``False`` the gate runs trust-on-first-use against ``pin_store``.
    identity_trust_bundle : EventSignatureTrustBundle or None
        Operator-managed identity keys (separate material from federation and
        signed-event trust). With binding required but no bundle configured the gate
        fails closed — it can verify nobody, so it admits nobody.
    send_json : Callable[[Any, dict], Awaitable[None]]
        The hub's per-socket send (``hub._send_json``), used to deliver the denial.
    system : Callable[..., dict]
        The hub's system-message factory (``hub._system``), used to stamp the denial
        with the hub id.
    pin_store : IdentityPinStore or None, optional
        The durable name→key pin table for the trust-on-first-use posture.
        ``None`` disables that posture, leaving the binding-off gate a pure
        pass-through (the pre-TOFU behaviour).
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
        pin_store: IdentityPinStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._require_identity_binding = require_identity_binding
        self._identity_trust_bundle = identity_trust_bundle
        self._send_json = send_json
        self._system = system
        self._pin_store = pin_store
        self._clock = clock
        self._tofu_replay = MessageReplayCache(
            window_seconds=DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS,
            max_entries=DEFAULT_IDENTITY_REPLAY_CAPACITY,
        )
        self._crypto_warning_logged = False

    def _warn_crypto_missing(self) -> None:
        """Log — once per gate — that trust-on-first-use is disabled.

        Without the optional ``cryptography`` package the gate can verify no
        proof, so pins are neither checked nor recorded. One warning names the
        remedy; repeating it per frame would drown the hub log.
        """
        if self._crypto_warning_logged:
            return
        self._crypto_warning_logged = True
        logger.warning(
            "trust-on-first-use identity pinning is disabled: the optional "
            "cryptography package is not installed (pip install "
            "synapse-channel[encryption]); signed registrations are admitted "
            "unverified"
        )

    async def verify_identity(self, sender: str, data: dict[str, Any], websocket: Any) -> bool:
        """Verify a first frame's identity proof; refuse and close on failure.

        With operator binding required, the frame must prove ``sender`` against
        the trust bundle — missing signature, unknown or revoked key, sender
        mismatch, expiry, replay, or a required-but-absent bundle all refuse.
        With binding off, the trust-on-first-use posture applies (see the class
        docstring): a pinned name must prove its pinned key; an unpinned name
        that presents a valid self-contained proof is pinned; an unpinned,
        unsigned name passes unchanged. Every refusal sends an error naming the
        reason and closes the socket (:data:`IDENTITY_BINDING_CLOSE_CODE`), and
        returns ``False`` so the hub stops routing before the name is bound.
        """
        if self._require_identity_binding:
            return await self._verify_against_bundle(sender, data, websocket)
        if self._pin_store is None:
            return True
        return await self._verify_first_use(self._pin_store, sender, data, websocket)

    async def _verify_against_bundle(
        self, sender: str, data: dict[str, Any], websocket: Any
    ) -> bool:
        """Run the operator-bundle posture: prove ``sender`` or be refused."""
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
        await self._refuse(
            websocket,
            sender,
            message=f"identity binding failed: {result.value}",
            reason="identity binding failed",
            verification_result=result.value,
        )
        return False

    async def _verify_first_use(
        self, pin_store: IdentityPinStore, sender: str, data: dict[str, Any], websocket: Any
    ) -> bool:
        """Run the trust-on-first-use posture against the pin store.

        The decision table, in order:

        - name pinned, frame proves the pinned key → admit;
        - name pinned, signature missing or proving anything else → refuse
          (the squatter path — omitting the signature must not bypass the pin);
        - name unpinned, frame carries a signature AND its public key, and the
          self-contained proof verifies → pin and admit (first use);
        - name unpinned, a presented proof FAILS → refuse (a broken proof is
          never admitted, and never pins);
        - name unpinned, no complete proof presented → admit unchanged (the
          pre-TOFU client, or a key provisioned for a different hub posture).

        A hub whose environment lacks the optional ``cryptography`` package
        cannot verify any proof: the posture degrades to a pass-through with
        a once-per-gate warning rather than refusing (which would brick every
        signing client) or crashing the frame handler. Trust-on-first-use is
        protection for the default local hub, never an availability hazard.
        """
        pin = pin_store.pinned(sender)
        presented_key = str(data.get("identity_public_key") or "")
        has_signature = isinstance(data.get("signature"), dict)
        if pin is not None:
            if not has_signature:
                await self._refuse_pin_mismatch(
                    pin_store, sender, websocket, detail="signature missing"
                )
                return False
            try:
                result = self._verify_self_contained(sender, data, pin.key_id, pin.public_key)
            except ImportError:
                self._warn_crypto_missing()
                return True
            if result is SignedEventVerificationResult.VALID:
                return True
            await self._refuse_pin_mismatch(pin_store, sender, websocket, detail=result.value)
            return False
        if not has_signature or not presented_key:
            return True
        key_id = str(data.get("signature", {}).get("key_id") or "")
        try:
            result = self._verify_self_contained(sender, data, key_id, presented_key)
        except ImportError:
            self._warn_crypto_missing()
            return True
        if result is not SignedEventVerificationResult.VALID:
            logger.warning("identity first-use proof failed for %s: %s", sender, result.value)
            await self._refuse(
                websocket,
                sender,
                message=f"identity signature invalid: {result.value}",
                reason="identity binding failed",
                verification_result=result.value,
            )
            return False
        pin_store.pin(sender, key_id=key_id, public_key=presented_key, now=self._clock())
        return True

    def _verify_self_contained(
        self, sender: str, data: dict[str, Any], key_id: str, public_key: str
    ) -> SignedEventVerificationResult:
        """Verify the frame against exactly one expected key.

        Builds a single-key ephemeral bundle around the shared replay cache, so
        freshness, replay, and sender binding come from the same primitives the
        operator-bundle posture uses — no parallel crypto path.
        """
        try:
            raw = base64.b64decode(public_key, validate=True)
        except (ValueError, binascii.Error):
            return SignedEventVerificationResult.UNKNOWN_KEY
        if len(raw) != _ED25519_RAW_PUBLIC_KEY_BYTES or not key_id:
            return SignedEventVerificationResult.UNKNOWN_KEY
        bundle = EventSignatureTrustBundle(
            keys={
                key_id: EventSignatureKey(
                    key_id=key_id, public_key=raw, senders=frozenset({sender})
                )
            },
            replay_cache=self._tofu_replay,
        )
        return verify_event_signature(
            data, trust_bundle=bundle, now=self._clock(), required_sender=sender
        )

    async def _refuse_pin_mismatch(
        self, pin_store: IdentityPinStore, sender: str, websocket: Any, *, detail: str
    ) -> None:
        """Refuse a claim on a pinned name with the actionable recovery path."""
        pin = pin_store.pinned(sender)
        pinned_key_id = pin.key_id if pin is not None else "unknown"
        store_hint = (
            f" after inspecting {pin_store.path}"
            if pin_store.path is not None
            else " after inspecting the in-memory pin through the hub operator"
        )
        reclaim_command = (
            "synapse identity reclaim "
            f"{shell_long_option('--operator', 'OPERATOR_IDENTITY')} "
            f"{shell_long_option('--expected-key-id', pinned_key_id)} "
            f"{shell_long_option('--reason', 'REASON')} -- {shell_command_arg(sender)}"
        )
        logger.warning(
            "identity pin refused name=%s pinned_key=%s detail=%s", sender, pinned_key_id, detail
        )
        await self._refuse(
            websocket,
            sender,
            message=(
                f"identity pin: name {sender!r} is pinned to key {pinned_key_id} "
                f"({detail}). Connect from the machine holding that key, or ask an "
                "identity-pin-reclaim ACL operator to run "
                f"{reclaim_command}{store_hint}; the hub needs --db for the mandatory audit."
            ),
            reason="identity pin mismatch",
            verification_result=detail,
        )

    async def _refuse(
        self,
        websocket: Any,
        sender: str,
        *,
        message: str,
        reason: str,
        verification_result: str,
    ) -> None:
        """Send the denial frame and close the socket with the identity code."""
        await self._send_json(
            websocket,
            self._system(
                message,
                msg_type=MessageType.ERROR,
                target=sender,
                verification_result=verification_result,
            ),
        )
        await websocket.close(code=IDENTITY_BINDING_CLOSE_CODE, reason=reason)
