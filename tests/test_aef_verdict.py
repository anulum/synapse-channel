# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent Evidence Format verdict contract tests

from __future__ import annotations

from synapse_channel.core.aef_verdict import AefVerdictCode


def test_aef_verdict_vocabulary_matches_the_accepted_boundary() -> None:
    assert {verdict.value for verdict in AefVerdictCode} == {
        "VALID",
        "VALID_LEGACY",
        "UNVERIFIABLE_TYPE",
        "MALFORMED",
        "UNSUPPORTED_VERSION",
        "INVALID_DOMAIN",
        "UNKNOWN_KEY",
        "REVOKED_KEY",
        "KEY_WINDOW_INVALID",
        "SENDER_SCOPE_MISMATCH",
        "INVALID_SIGNATURE",
        "INVALID_RECEIPT_ID",
        "EXPIRED",
        "REPLAYED",
        "CHAIN_CONFLICT",
        "UNTRUSTED_LOG",
    }
