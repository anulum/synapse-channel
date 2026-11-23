# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the A2A access boundary
"""The token, origins and authorities one A2A bridge admits."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from synapse_channel.a2a_http_protocol import normalise_authority, normalise_origin


@dataclass(frozen=True, slots=True)
class A2AAccessPolicy:
    """One frozen access boundary.

    Construction normalises origins and authorities and refuses an Origin
    allow-list that has no trusted authority. A later replacement stores the
    assigned authorities as given.
    """

    auth_token: str | None
    allowed_origins: tuple[str, ...]
    allowed_authorities: tuple[str, ...]

    @staticmethod
    def normalised(
        allowed_origins: Sequence[str],
        allowed_authorities: Sequence[str],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return normalised origins and authorities, in that order.

        Raises
        ------
        ValueError
            When an Origin allow-list has no trusted endpoint authority.
        """
        origins = tuple(normalise_origin(origin) for origin in allowed_origins)
        authorities = tuple(normalise_authority(authority) for authority in allowed_authorities)
        if origins and not authorities:
            raise ValueError("an Origin allow-list requires a trusted endpoint authority")
        return origins, authorities
