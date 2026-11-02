# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — atomic lease-token write when the final rename fails
"""A lease path that cannot be replaced keeps no temporary token behind."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.owner_lease import save_lease


def test_rename_onto_a_directory_fails_and_removes_the_temporary_token(tmp_path: Path) -> None:
    """os.replace onto a directory raises; the written temporary file is unlinked."""
    lease = tmp_path / "leases" / "P__agent.lease"
    lease.mkdir(parents=True)
    (lease / "keep").write_text("unrelated", encoding="utf-8")
    with pytest.raises(OSError):
        save_lease(lease, "token-that-must-not-linger")
    assert sorted(path.name for path in lease.parent.iterdir()) == ["P__agent.lease"]
    assert [path.name for path in lease.iterdir()] == ["keep"]
