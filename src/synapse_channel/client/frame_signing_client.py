# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — sign a connection's frames when its hub requires it
"""Follow one connection's signing session and sign the frames sent on it.

A hub that requires signed frames says so in its welcome, with its hub id
and the connection's session nonce. The client then signs every frame it
sends on that connection, apart from the registration frame, with the
identity key it registered with, numbering them from one.
A new connection brings a new welcome and so a new session.

A hub that does not ask for signatures gets unchanged frames, so an old hub
sees nothing new.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.core.frame_signature import sign_session_frame

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REQUIRED = "required"
"""Welcome value of ``frame_signatures`` on a hub that requires signed frames."""


@dataclass
class ClientFrameSession:
    """The signing session of one connection.

    Parameters
    ----------
    hub_id : str
        The hub named in the welcome.
    session_nonce : str
        The connection's session nonce from the welcome.
    seq : int, optional
        Sequence of the last signed frame; the next one is ``seq + 1``.
    """

    hub_id: str
    session_nonce: str
    seq: int = 0


def session_from_welcome(welcome: Mapping[str, Any]) -> ClientFrameSession | None:
    """Return the signing session a welcome asks for, or ``None`` when it asks for none."""
    hub_id = welcome.get("hub_id")
    nonce = welcome.get("session_nonce")
    if welcome.get("frame_signatures") != REQUIRED:
        return None
    if not isinstance(hub_id, str) or not hub_id or not isinstance(nonce, str) or not nonce:
        return None
    return ClientFrameSession(hub_id, nonce)


def sign_outbound(
    frame: dict[str, Any],
    session: ClientFrameSession,
    *,
    key_id: str,
    private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    """Return ``frame`` signed for ``session``.

    Parameters
    ----------
    frame : dict[str, Any]
        The complete frame, after any HMAC ``auth`` is attached.
    session : ClientFrameSession
        The connection's session; its sequence advances on every signature.
    key_id : str
        The identity key the connection registered with.
    private_key : Ed25519PrivateKey
        That key.

    Returns
    -------
    dict[str, Any]
        The frame to send.

    Raises
    ------
    FrameCanonicalError
        If the frame holds a value a signature cannot cover: an integer beyond
        ±(2**53 − 1), a non-finite number or a lone surrogate. Nothing is sent.
    """
    signed = sign_session_frame(
        frame,
        key_id=key_id,
        private_key=private_key,
        hub_id=session.hub_id,
        session_nonce=session.session_nonce,
        seq=session.seq + 1,
    )
    session.seq += 1
    return signed
