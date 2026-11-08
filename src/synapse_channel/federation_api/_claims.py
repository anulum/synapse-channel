# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation interface: claim forwarding
"""Claim forwarding: the federation interface names of this capability.

Forward a claim to the hub that owns its task and read the owner's answer. Import these
names from ``synapse_channel.federation_api``; this module only groups them.
"""

from __future__ import annotations

from synapse_channel.core.multihub_claim_transport import (
    DEFAULT_FORWARD_TIMEOUT,
    ClaimForwardError,
    forward_claim,
)
from synapse_channel.core.multihub_claim_wire import (
    ClaimForwardRequest,
    ClaimForwardResult,
    ClaimWireError,
    encode_claim_forward_request,
)

__all__ = [
    "ClaimForwardError",
    "ClaimForwardRequest",
    "ClaimForwardResult",
    "ClaimWireError",
    "DEFAULT_FORWARD_TIMEOUT",
    "encode_claim_forward_request",
    "forward_claim",
]
