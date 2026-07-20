# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent Evidence Format verification verdicts
"""Closed verification outcomes for Agent Evidence Format boundaries."""

from __future__ import annotations

from enum import Enum


class AefVerdictCode(str, Enum):
    """Machine-readable outcome of an AEF or legacy-receipt verification.

    ``VALID`` is reserved for receipts verified under the accepted AEF profile.
    ``VALID_LEGACY`` identifies receipts that pass the historical Synapse
    signature rules without upgrading their weaker serialization, anchoring, or
    time semantics. Advisory producer metadata such as ``epistemic_status`` is
    never converted into either valid verdict.
    """

    VALID = "VALID"
    VALID_LEGACY = "VALID_LEGACY"
    UNVERIFIABLE_TYPE = "UNVERIFIABLE_TYPE"
    MALFORMED = "MALFORMED"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    INVALID_DOMAIN = "INVALID_DOMAIN"
    UNKNOWN_KEY = "UNKNOWN_KEY"
    REVOKED_KEY = "REVOKED_KEY"
    KEY_WINDOW_INVALID = "KEY_WINDOW_INVALID"
    SENDER_SCOPE_MISMATCH = "SENDER_SCOPE_MISMATCH"
    INVALID_SIGNATURE = "INVALID_SIGNATURE"
    INVALID_RECEIPT_ID = "INVALID_RECEIPT_ID"
    EXPIRED = "EXPIRED"
    REPLAYED = "REPLAYED"
    CHAIN_CONFLICT = "CHAIN_CONFLICT"
    UNTRUSTED_LOG = "UNTRUSTED_LOG"
