# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — central WebSocket hub that routes messages and owns state
"""Typed legacy keyword names; canonical defaults remain in HubConfig."""

from __future__ import annotations

from typing import TypedDict


class HubLimitsOptions(TypedDict, total=False):
    """Preserve the original limits keyword value contracts."""

    max_history: int
    max_progress: int
    max_progress_per_author: int
    max_progress_per_task: int
    board_task_cap: int | None
    max_findings_per_agent: int
    compact_hint_threshold: int
    dead_letter_escalation_threshold: int
    max_clients: int
    max_unauth_clients: int | None
    max_connections_per_host: int | None
    max_msg_bytes: int
    max_claims_per_agent: int
    max_offers_per_agent: int
    max_paths_per_claim: int
