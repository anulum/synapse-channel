# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — continuous lease ownership checks
"""Read authoritative continuous ownership without trusting epoch caches."""

from __future__ import annotations

from collections.abc import Mapping


def claim_continues(claim: Mapping[str, object], owner: str, original_epoch: int | None) -> bool:
    """Require the same owner and a genuine current epoch or ownership anchor."""
    epoch, anchor = claim.get("epoch"), claim.get("ownership_epoch")
    return (
        claim.get("owner") == owner
        and type(epoch) is int
        and epoch > 0
        and (original_epoch == epoch or (type(anchor) is int and original_epoch == anchor))
    )
