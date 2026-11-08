# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation interface: health and entitlement
"""Health and entitlement: the federation interface names of this capability.

A hub's local fleet health document and entitlement advert validation. Import these
names from ``synapse_channel.federation_api``; this module only groups them.
"""

from __future__ import annotations

from synapse_channel.core.entitlement_advert import (
    EntitlementAdvertError,
    validate_advert,
)
from synapse_channel.core.fleet_health import (
    build_local_fleet_health,
)

__all__ = [
    "EntitlementAdvertError",
    "build_local_fleet_health",
    "validate_advert",
]
