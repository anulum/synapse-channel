# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one advisory lock, held and refused, on this host
"""The public advisory lock holds one file and refuses a second taker."""

from __future__ import annotations

from pathlib import Path

from synapse_channel.advisory_lock import locked


def test_a_second_taker_does_not_hold_the_lock(tmp_path: Path) -> None:
    """One holder excludes another, and the file can be held again after release."""
    path = tmp_path / "nest" / "lock"

    with locked(path, blocking=False, purpose="advisory lock test") as held:
        assert held is True
        with locked(path, blocking=False, purpose="advisory lock test") as other:
            assert other is False

    with locked(path, blocking=False, purpose="advisory lock test") as again:
        assert again is True
