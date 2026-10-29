# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — retained observation topology corruption refusals
"""Corrupt real retained storage while preserving digest and commit-marker consistency.

The existing completed execution fixture exercises local storage mechanics.
Its documented current/isolation callbacks do not prove physical custody.
These tests assert corruption refusal only, never privileged writer acceptance.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.persistence import StoredOperation
from synapse_channel.core.protected_write_execution_observations import (
    verify_protected_execution_observations,
)
from test_protected_write_execution_journal import completed_execution as completed_execution
from test_protected_write_execution_observations import Execution, evidence_store


@pytest.mark.parametrize("case", ["directory_substitution", "create_alias"])
def test_digest_valid_topology_substitution_refuses(
    completed_execution: Execution, tmp_path: Path, case: str
) -> None:
    """Real journal/evidence corruption cannot turn an unrelated inode into an effect."""
    prepared, begun, service, records = completed_execution
    evidence = evidence_store(completed_execution)
    record = records[1]
    document = json.loads(evidence.read(record.response["evidence_reference"], 4096))
    observed = document["after"][0]
    if case == "directory_substitution":
        unrelated = tmp_path / "unrelated"
        unrelated.mkdir(mode=0o700)
        actual = unrelated.stat()
        observed["directories"][-1] = [actual.st_dev, actual.st_ino]
        message = "changed a retained directory identity"
    else:
        observed["identity"] = observed["directories"][0]
        message = "new object aliases an existing physical object"
    rewrite_retained_observation(completed_execution, tmp_path, record, document)
    before = service.read_all()
    with pytest.raises(ValueError, match=message):
        verify_protected_execution_observations(
            prepared,
            begun,
            service_journal=service,
            read_evidence=evidence.read,
            max_total_evidence_bytes=65536,
        )
    assert service.read_all() == before


def rewrite_retained_observation(
    execution: Execution,
    tmp_path: Path,
    record: StoredOperation,
    document: dict[str, Any],
) -> None:
    """Persist corrupt bytes and rehash their entire actual continuation chain."""
    _prepared, _begun, service, _records = execution
    evidence = evidence_store(execution)
    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    reference = evidence.write(encoded)
    body = {**record.response, "evidence_reference": dict(reference)}
    replacements: dict[str, str] = {}
    with sqlite3.connect(tmp_path / "completed.db") as database:
        rows = database.execute(
            "SELECT operation_key,response_json,response_sha256,first_event_seq,commit_seq "
            "FROM operations ORDER BY first_event_seq"
        ).fetchall()
        for key, original, original_digest, first_sequence, commit_sequence in rows:
            changed = body if key == record.operation_key else json.loads(original)
            for field in ("predecessor_response_sha256", "predecessor_request_digest"):
                if changed.get(field) in replacements:
                    changed[field] = replacements[changed[field]]
            raw = json.dumps(changed, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            if digest == original_digest:
                continue
            replacements[original_digest] = digest
            marker = service.latest_at_or_before(commit_sequence)
            assert marker is not None
            payload = {
                **marker.payload,
                "request_digest": digest,
                "response_sha256": digest,
                "response": changed,
            }
            database.execute(
                "UPDATE operations SET response_json=?, response_sha256=?, request_digest=? "
                "WHERE operation_key=?",
                (raw, digest, digest, key),
            )
            database.execute("UPDATE events SET payload=? WHERE seq=?", (raw, first_sequence))
            database.execute(
                "UPDATE events SET payload=? WHERE seq=?", (json.dumps(payload), commit_sequence)
            )
