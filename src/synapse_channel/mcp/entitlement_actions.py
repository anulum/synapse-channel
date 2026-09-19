# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — redacted local entitlement MCP action
"""Keep private ledger fields out of the MCP coordination face."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from synapse_channel.core.entitlement_store import (
    EntitlementStoreError,
    default_entitlement_store,
    read_events,
)
from synapse_channel.core.entitlement_view import entitlement_view
from synapse_channel.core.entitlements import EntitlementError


def read_entitlement_overview() -> str:
    """Return a label-free local report or a generic unavailable state.

    An MCP identity does not prove owner authority. No account identifiers,
    product labels, balances, sources or credential references leave this face.
    """
    try:
        report = entitlement_view(
            read_events(default_entitlement_store()),
            as_of=datetime.now(timezone.utc),
            private=False,
        )
    except (EntitlementStoreError, EntitlementError, ValueError):
        return json.dumps({"available": False, "reason": "private ledger unavailable"})
    return json.dumps(report, sort_keys=True)
