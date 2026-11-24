# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation interface: operation record projection
"""Operation record projection: the federation interface names of this capability.

Imported on demand by :mod:`synapse_channel.federation_api` when a caller reads
one of these names. Consumers import from :mod:`synapse_channel.federation_api`,
not from this private submodule.
"""

from __future__ import annotations

from synapse_channel.core.operation_record_reader import (
    OPERATION_RECORD_KINDS,
    SCOPE_STOP_RECORD_KINDS,
    IndependentVerificationRecord,
    OperationAttemptSnapshot,
    OperationEvent,
    OperationEvidenceSnapshot,
    OperationMalformedRecord,
    OperationRecordReader,
    OperationRecordSnapshot,
)

__all__ = [
    "IndependentVerificationRecord",
    "OPERATION_RECORD_KINDS",
    "OperationAttemptSnapshot",
    "OperationEvent",
    "OperationEvidenceSnapshot",
    "OperationMalformedRecord",
    "OperationRecordReader",
    "OperationRecordSnapshot",
    "SCOPE_STOP_RECORD_KINDS",
]
