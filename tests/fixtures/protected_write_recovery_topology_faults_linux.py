# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — modeled parent recovery corruption refusal
"""Require an explicit retained state even when a child names a real parent inode."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from fixtures.protected_write_recovery_snapshot_linux import (
    RecoveryFixture,
)
from fixtures.protected_write_recovery_snapshot_linux import (
    recovery_fixture as recovery_fixture,
)
from synapse_channel.core.protected_write_execution_journal import record_protected_execution_start
from synapse_channel.core.protected_write_recovery_snapshot import (
    inspect_protected_recovery_snapshot,
)


def test_inferred_parent_inode_cannot_replace_missing_parent_state(
    recovery_fixture: RecoveryFixture,
) -> None:
    """Real child descriptors establish an inode, never a missing parent byte state."""
    prepared, begun, service, evidence, root = recovery_fixture
    record_protected_execution_start(
        prepared, begun, service_journal=service, max_journal_operations=64
    )
    observations = dict(prepared.observations)
    child = observations[("memory", "records/note.md")]
    assert child is not None and len(child.directories) == 2
    assert observations[("memory", "records")] is not None
    observations[("memory", "records")] = None
    corrupted = replace(prepared, observations=tuple(observations.items()))
    before = service.read_all()
    with pytest.raises(ValueError, match="recovery path has no modeled state"):
        inspect_protected_recovery_snapshot(
            corrupted,
            begun,
            service_journal=service,
            read_evidence=evidence.read,
            max_total_evidence_bytes=16384,
            root_descriptors={"memory": root, "parent": root},
            owner_uid=os.getuid(),
            max_total_content_bytes=16384,
        )
    assert service.read_all() == before
