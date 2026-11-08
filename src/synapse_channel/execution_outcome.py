# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — scoped execution observations
"""Share execution truth without equating a transport result with an external effect.

The record lives in :mod:`synapse_channel.core.execution_outcome`, because the
hub validates and journals outcome reports. This module keeps the edge import
path unchanged.
"""

from __future__ import annotations

from synapse_channel.core.execution_outcome import (
    OUTCOME_SCHEMA,
    ExecutionOutcome,
    OutcomeBoundary,
    OutcomeState,
    outcome_from_json,
)

__all__ = [
    "OUTCOME_SCHEMA",
    "ExecutionOutcome",
    "OutcomeBoundary",
    "OutcomeState",
    "outcome_from_json",
]
