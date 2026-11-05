# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — which identity key may sign each socket's frames
"""Remember, per socket, the identity key that proved it and the session it signs in.

A socket that registers with a verified identity signature proved one key:
an operator-enrolled key from the identity trust bundle, or the key pinned to
its name on first use. That key is the only one that may sign the socket's
later frames. This module keeps the record; it does not verify anything.

Only the key identifier and its source are kept. The public key is looked up
again whenever a frame is checked, so a key that is revoked, rotated or whose
pin is reclaimed stops matching at once.

Each socket also gets a random session nonce, sent in its welcome. A signed
frame names it, so a frame captured on one connection cannot be replayed on
another.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Literal

SESSION_NONCE_BYTES = 18
"""Random bytes in a session nonce (144 bits, 24 URL-safe characters)."""

KeySource = Literal["bundle", "pin"]
"""Where a socket's signing key comes from: the operator trust bundle or a TOFU pin."""


@dataclass
class FrameSigner:
    """The identity that proved one socket.

    Parameters
    ----------
    sender : str
        The name the socket bound with the proof.
    key_id : str
        Identifier of the key that signed the registration.
    source : {"bundle", "pin"}
        Where the key is looked up when a frame is checked.
    last_seq : int, optional
        Highest frame sequence accepted on the socket so far.
    """

    sender: str
    key_id: str
    source: KeySource
    last_seq: int = 0


class FrameSigningSessions:
    """Per-socket signing identities and session nonces for one hub."""

    def __init__(self) -> None:
        self._signers: dict[Any, FrameSigner] = {}
        self._nonces: dict[Any, str] = {}

    def __len__(self) -> int:
        """Return the number of sockets with a recorded signing key."""
        return len(self._signers)

    def session_nonce(self, websocket: Any) -> str:
        """Return the socket's session nonce, creating it on first use."""
        nonce = self._nonces.get(websocket)
        if nonce is None:
            nonce = secrets.token_urlsafe(SESSION_NONCE_BYTES)
            self._nonces[websocket] = nonce
        return nonce

    def welcome_fields(self, websocket: Any) -> dict[str, str]:
        """Return the fields this module adds to a socket's welcome frame."""
        return {"session_nonce": self.session_nonce(websocket)}

    def record(self, websocket: Any, sender: str, key_id: str, source: KeySource) -> None:
        """Record that ``websocket`` proved ``sender`` with ``key_id`` from ``source``."""
        self._signers[websocket] = FrameSigner(sender, key_id, source)

    def signer(self, websocket: Any, sender: str) -> FrameSigner | None:
        """Return the socket's signing identity while it is still bound to ``sender``."""
        signer = self._signers.get(websocket)
        if signer is None or signer.sender != sender:
            return None
        return signer

    def forget(self, websocket: Any) -> None:
        """Drop everything kept for a socket that closed."""
        self._signers.pop(websocket, None)
        self._nonces.pop(websocket, None)
