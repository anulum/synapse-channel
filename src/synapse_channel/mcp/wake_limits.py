# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — finite wake storage limits
"""Define finite per-file and aggregate wake-storage admission budgets."""

from __future__ import annotations

from dataclasses import dataclass

WAKE_DATABASE_APPLICATION_ID = 0x53574B31

MAX_WAKE_DATABASE_BYTES = 4 * 1024 * 1024
MAX_WAKE_RECEIPTS = 64
MAX_WAKE_JOURNAL_BYTES = 8 * 1024 * 1024
MAX_ROOT_LEDGER_SLOTS = 1024
MAX_RETIRED_GENERATIONS = 4096
ROOT_CATALOGUE = ".root.sqlite3"
ROOT_LOCK = ".root.lock"
ROOT_INITIALIZED_LOCK_BYTES = 2
ROOT_TRANSACTION_HEADROOM = MAX_WAKE_DATABASE_BYTES + 2 * MAX_WAKE_JOURNAL_BYTES
MAX_ROOT_FILES = 2 * MAX_ROOT_LEDGER_SLOTS + 3


@dataclass(frozen=True)
class RootLimits:
    """Bound admitted generations and aggregate logical file bytes.

    Parameters
    ----------
    max_ledgers : int
        Simultaneous current/reserved/archive-in-progress generations.
    max_bytes : int
        Root budget including conservative transactional maintenance headroom.
        This is an application admission budget, not an operating-system quota.
    """

    max_ledgers: int = 128
    max_bytes: int = 512 * 1024 * 1024

    def __post_init__(self) -> None:
        """Reject coerced, zero or unbounded operator limits."""
        if (
            type(self.max_ledgers) is not int
            or not 1 <= self.max_ledgers <= MAX_ROOT_LEDGER_SLOTS
            or type(self.max_bytes) is not int
            or not 32 * 1024 * 1024 <= self.max_bytes <= 16 * 1024**3
        ):
            raise ValueError("invalid wake root limits")
