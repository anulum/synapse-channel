# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation interface: protocol and client
"""Protocol and client: the federation interface names of this capability.

The wire version and its negotiation, message types, and the Python client. Import these
names from ``synapse_channel.federation_api``; this module only groups them.
"""

from __future__ import annotations

from synapse_channel.client.agent import (
    SynapseAgent,
)
from synapse_channel.core.protocol import (
    MessageType,
    ProtocolNegotiation,
)

__all__ = [
    "MessageType",
    "ProtocolNegotiation",
    "SynapseAgent",
]
