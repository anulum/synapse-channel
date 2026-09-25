# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — complete protected commit evidence
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from synapse_channel.core.atomic_operations import canonical_request_digest
from synapse_channel.core.protected_write_journal import protected_operation_response
from test_protected_write_admission import _admission
from test_protected_write_admission_journal import _committed
from test_protected_write_result import _result


def test_shared_commit_evidence_returns_exact_response(tmp_path: Path) -> None:
    store = _committed(tmp_path)
    request, _ = _result("admit")
    response = protected_operation_response(
        store,
        operation_key=_admission().operation_key,
        request_digest=canonical_request_digest(request),
        mutation_sequence=2,
        through_seq=3,
    )
    assert json.loads(response)["body"]["admission_sequence"] == 2
    assert store.count() == 3
    store.close()


def test_commit_marker_preserves_exact_numeric_types(tmp_path: Path) -> None:
    store = _committed(tmp_path)
    marker = store.latest_at_or_before(3)
    assert marker is not None
    payload = marker.payload
    payload["response"]["body"]["admission_sequence"] = 2.0
    with sqlite3.connect(tmp_path / "admission-replay.db") as connection:
        connection.execute("UPDATE events SET payload = ? WHERE seq = 3", (json.dumps(payload),))
    request, _ = _result("admit")
    with pytest.raises(ValueError, match="commit marker mismatch"):
        protected_operation_response(
            store,
            operation_key=_admission().operation_key,
            request_digest=canonical_request_digest(request),
            mutation_sequence=2,
        )
    store.close()


@pytest.mark.parametrize("sequence", [True, 0, -1, 2.0])
def test_shared_commit_evidence_rejects_invalid_sequence(tmp_path: Path, sequence: object) -> None:
    store = _committed(tmp_path)
    request, _ = _result("admit")
    with pytest.raises(ValueError, match="mutation sequence"):
        protected_operation_response(
            store,
            operation_key=_admission().operation_key,
            request_digest=canonical_request_digest(request),
            mutation_sequence=sequence,  # type: ignore[arg-type]
        )
    store.close()
