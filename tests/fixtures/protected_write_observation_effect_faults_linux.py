# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actual rename and unlink retained observation faults

"""Corrupt real retained rename/unlink histories without granting writer authority."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fixtures.protected_write_observation_topology_faults_linux import rewrite_retained_observation
from protected_write_effect_history import completed_effect as completed_effect
from synapse_channel.core.protected_write_evidence_store import ProtectedExecutionEvidenceStore
from synapse_channel.core.protected_write_execution_observations import (
    verify_protected_execution_observations,
)
from test_protected_write_execution_observations import Execution


def test_real_effect_history_then_false_inode_refuses(
    completed_effect: Execution, tmp_path: Path
) -> None:
    """Real rename retains its source inode and real unlink has no live inode."""
    prepared, begun, service, records = completed_effect
    evidence = ProtectedExecutionEvidenceStore(
        service, "text", prepared.policy.limits.operation_limits, 4096, 128
    )
    assert verify_protected_execution_observations(
        prepared,
        begun,
        service_journal=service,
        read_evidence=evidence.read,
        max_total_evidence_bytes=65536,
    )
    record = next(item for item in records if item.response.get("operation_id") == "effect-scratch")
    document = json.loads(evidence.read(record.response["evidence_reference"], 4096))
    operation = next(
        item
        for item in json.loads(prepared.admission.proposal_bytes)["auxiliary_operations"]
        if item["operation_id"] == "effect-scratch"
    )
    if operation["opcode"] == "rename":
        document["after"][1]["identity"] = document["after"][1]["directories"][0]
        message = "rename destination did not retain source identity"
    else:
        document["after"][0]["identity"] = document["after"][0]["directories"][0]
        message = "absent retained object carries a filesystem identity"
    rewrite_retained_observation(completed_effect, tmp_path, record, document)
    with pytest.raises(ValueError, match=message):
        verify_protected_execution_observations(
            prepared,
            begun,
            service_journal=service,
            read_evidence=evidence.read,
            max_total_evidence_bytes=65536,
        )
