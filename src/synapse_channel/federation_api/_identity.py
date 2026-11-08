# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation interface: identity, tls and keys at rest
"""Identity, TLS and keys at rest: the federation interface names of this capability.

Identity signing keys, peer registration signatures, TLS pins and encrypted journals.
Import these names from ``synapse_channel.federation_api``; this module only groups
them.
"""

from __future__ import annotations

from synapse_channel.core.at_rest import (
    load_key_file,
)
from synapse_channel.core.identity_keys import (
    IdentityKeyError,
    load_signing_key,
)
from synapse_channel.core.peer_identity import (
    PeerRegistrationSigner,
    load_peer_registration_signer,
)
from synapse_channel.core.persistence_sqlcipher import (
    apply_sqlcipher_key,
    import_sqlcipher_module,
)
from synapse_channel.core.tls import (
    MTLSPeerTrustBundle,
    MTLSTrustedPeer,
    certificate_sha256_pin,
    certificate_sha256_pin_from_der,
)

__all__ = [
    "IdentityKeyError",
    "MTLSPeerTrustBundle",
    "MTLSTrustedPeer",
    "PeerRegistrationSigner",
    "apply_sqlcipher_key",
    "certificate_sha256_pin",
    "certificate_sha256_pin_from_der",
    "import_sqlcipher_module",
    "load_key_file",
    "load_peer_registration_signer",
    "load_signing_key",
]
