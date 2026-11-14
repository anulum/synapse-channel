# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — historical text of a string enumeration member
"""Keep string enumerations rendering their member name."""

from __future__ import annotations

from enum import Enum
from typing import cast


class MemberText:
    """Keep ``str`` and ``format`` on the enumeration member name.

    A :class:`enum.StrEnum` renders its wire value. These methods keep the
    historical member text, ``Type.MEMBER``.
    """

    def __str__(self) -> str:
        """Return ``Type.MEMBER``."""
        return Enum.__str__(cast(Enum, self))

    def __format__(self, format_spec: str) -> str:
        """Format ``Type.MEMBER`` with ``format_spec``."""
        return Enum.__format__(cast(Enum, self), format_spec)
