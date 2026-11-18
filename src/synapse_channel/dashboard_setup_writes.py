# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the dashboard's setup write routes, behind one entry
"""Route one setup write, ``POST /setup/plan`` or ``POST /setup/apply``, to its module.

The dashboard's handler holds one entry for both, so the setup writes stay
behind one seam: the request is read once, with its body bounded, and the
start's access policy, setup decision, inspection and rate limiter are lent to
the route.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from email.message import Message
from typing import Final

from synapse_channel.dashboard_access import DashboardAccessPolicy
from synapse_channel.dashboard_access_http import AccessHttpDecision
from synapse_channel.dashboard_operator import WriteRateLimiter
from synapse_channel.dashboard_setup_apply_http import (
    SETUP_APPLY_PATH,
    SetupApplyBindings,
    setup_apply_response,
)
from synapse_channel.dashboard_setup_http import Inspection, SetupServing
from synapse_channel.dashboard_setup_plan_http import (
    SETUP_PLAN_PATH,
    SetupPlanBindings,
    setup_plan_response,
)
from synapse_channel.dashboard_setup_write_http import read_setup_write_request

SETUP_WRITE_PATHS: Final = frozenset({SETUP_PLAN_PATH, SETUP_APPLY_PATH})
"""Every setup write route; each answers 404 naming the reason when setup is not served."""


@dataclass(frozen=True)
class SetupWriteContext:
    """What one dashboard start lends its setup writes.

    ``hub_token`` is the dashboard's shared secret for its hub, when it has one.
    The apply hands it to the executor's own inspection in its environment, as
    the command line reads it; no answer carries it.
    """

    policy: DashboardAccessPolicy
    serving: SetupServing
    inspect: Inspection
    rate_limiter: WriteRateLimiter
    hub_token: str | None = None


def setup_write_response(
    route: str, headers: Message, rfile: io.BufferedIOBase, context: SetupWriteContext
) -> AccessHttpDecision:
    """Answer one setup write on ``route``, one of :data:`SETUP_WRITE_PATHS`."""
    request = read_setup_write_request(headers, rfile)
    if route == SETUP_PLAN_PATH:
        bindings = SetupPlanBindings(
            context.policy, context.serving, context.inspect, context.rate_limiter
        )
        return setup_plan_response(request, bindings)
    environment = dict(os.environ)
    if context.hub_token:
        environment["SYNAPSE_TOKEN"] = context.hub_token
    apply = SetupApplyBindings(
        context.policy, context.serving, context.inspect, context.rate_limiter, environment
    )
    return setup_apply_response(request, apply)
