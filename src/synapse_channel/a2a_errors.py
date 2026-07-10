# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — typed Agent2Agent boundary failures
"""Stable error codes for Agent2Agent request and storage failures."""

from __future__ import annotations

from synapse_channel.core.errors import SynapseError


class A2AError(SynapseError, ValueError):
    """Base for A2A failures that retain legacy ``ValueError`` handling."""

    code = "a2a"


class A2AValidationError(A2AError):
    """Raised when caller-controlled A2A input is malformed or unsafe."""

    code = "a2a_validation"


class A2AConflictError(A2AError):
    """Raised when an A2A request conflicts with existing task state."""

    code = "a2a_conflict"


class A2ANotFoundError(A2AError):
    """Raised when an A2A resource requested by id does not exist."""

    code = "a2a_not_found"


class A2AQuotaError(A2AError):
    """Raised when an A2A request exceeds an explicit bounded resource limit."""

    code = "a2a_quota"


class A2AStoreError(A2AError):
    """Raised when persisted A2A state cannot be decoded safely."""

    code = "a2a_store"
