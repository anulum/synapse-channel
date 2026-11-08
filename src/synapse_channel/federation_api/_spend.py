# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation interface: shared spend
"""Shared spend: the federation interface names of this capability.

Reserve from a pool another hub owns and honour the owner's grants and revocations.
Import these names from ``synapse_channel.federation_api``; this module only groups
them.
"""

from __future__ import annotations

from synapse_channel.core.spend_epoch import (
    OwnerRevocation,
    SpendEpochError,
    usable_grant,
    verify_owner_revocation,
)
from synapse_channel.core.spend_transport import (
    DEFAULT_SPEND_TIMEOUT,
    Connector,
    SpendTransportError,
    SpendTransportTimeoutError,
    request_spend,
)

__all__ = [
    "Connector",
    "DEFAULT_SPEND_TIMEOUT",
    "OwnerRevocation",
    "SpendEpochError",
    "SpendTransportError",
    "SpendTransportTimeoutError",
    "request_spend",
    "usable_grant",
    "verify_owner_revocation",
]
