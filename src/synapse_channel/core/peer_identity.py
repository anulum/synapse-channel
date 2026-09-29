# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — let a peer hub prove its id with a registration signature
"""Sign a hub-to-hub transport's first frame like a seat's registration.

A hub that requires identity binding admits a peer hub's frame when either the
peer's pinned mutual-TLS certificate proves the name (a serving grant), or the
frame carries an identity signature verified against the trust bundle. A
TLS-terminating proxy removes the client certificate, so only the signature
survives it. :class:`PeerRegistrationSigner` adds that signature to the
multi-hub pull, claim, message, operator-relay and dead-letter frames. The key
is an ordinary identity key: enrol it on each peer for this hub's id, for
example with ``synapse identity machine-key --sender <hub-id> --trust FILE``.
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from synapse_channel.core.identity_keys import load_signing_key, sign_registration

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class PeerRegistrationSigner:
    """Sign each first frame a hub sends to a peer with one identity key.

    Parameters
    ----------
    private_key : Ed25519PrivateKey
        The hub's identity signing key.
    key_id : str
        The key id the peers' trust bundles know it by.
    """

    def __init__(self, private_key: Ed25519PrivateKey, key_id: str) -> None:
        if not key_id.strip():
            raise ValueError("a peer identity key needs a non-empty key id")
        self._private_key = private_key
        self.key_id = key_id.strip()
        self._sequence = 0

    def sign(self, frame: dict[str, Any]) -> dict[str, Any]:
        """Return ``frame`` with a single-use identity signature.

        The sequence is derived from the clock in microseconds and never goes
        backwards within this signer, so a restarted hub keeps rising too.
        """
        self._sequence = max(self._sequence + 1, time.time_ns() // 1000)
        return sign_registration(
            frame,
            private_key=self._private_key,
            key_id=self.key_id,
            nonce=secrets.token_urlsafe(18),
            sequence=self._sequence,
        )


def load_peer_registration_signer(path: str | Path, key_id: str) -> PeerRegistrationSigner:
    """Load an owner-only Ed25519 PEM identity key as a peer registration signer.

    Raises
    ------
    IdentityKeyError
        When the key file is unreadable, not owner-only, or not Ed25519.
    ValueError
        When ``key_id`` is empty.
    """
    return PeerRegistrationSigner(load_signing_key(path), key_id)


def signed(frame: dict[str, Any], signer: PeerRegistrationSigner | None) -> dict[str, Any]:
    """Return ``frame`` signed by ``signer``, or unchanged without one."""
    return frame if signer is None else signer.sign(frame)
