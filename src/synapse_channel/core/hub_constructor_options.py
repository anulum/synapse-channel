# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — central WebSocket hub that routes messages and owns state
"""Resolve grouped construction with an exact typed legacy keyword boundary."""

from __future__ import annotations

from collections.abc import Mapping

from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.hub_options_auth import HubAuthOptions
from synapse_channel.core.hub_options_general import HubGeneralOptions
from synapse_channel.core.hub_options_limits import HubLimitsOptions
from synapse_channel.core.hub_options_routing import HubRoutingOptions


class HubLegacyOptions(
    HubGeneralOptions, HubLimitsOptions, HubAuthOptions, HubRoutingOptions, total=False
):
    """All original keyword names and types, with defaults owned solely by records."""


def resolve_hub_config(config: HubConfig | None, legacy: Mapping[str, object]) -> HubConfig:
    """Resolve one construction source and refuse contradictory grouped/flat input."""
    if config is not None:
        if not isinstance(config, HubConfig):
            raise TypeError("config must be a HubConfig record")
        if legacy:
            raise TypeError("cannot combine a HubConfig record with legacy keyword options")
        return config
    return HubConfig.from_kwargs(legacy)
