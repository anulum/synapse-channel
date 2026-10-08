# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — central WebSocket hub that routes messages and owns state
"""Existing setting names backed by one grouped hub configuration record."""

from synapse_channel.core.hub_config_auth_view import HubAuthConfigView
from synapse_channel.core.hub_config_limits_view import HubLimitsConfigView
from synapse_channel.core.hub_config_metrics_view import HubMetricsConfigView
from synapse_channel.core.hub_config_routing_view import HubRoutingConfigView


class HubConfigView(
    HubAuthConfigView, HubLimitsConfigView, HubMetricsConfigView, HubRoutingConfigView
):
    """Preserve legacy settings without duplicating them in the instance dictionary."""
