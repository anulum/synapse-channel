# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — analysis CLIs open SQLCipher event stores

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import sqlcipher_available
from synapse_channel.core.state import TaskClaim

pytestmark = pytest.mark.skipif(
    not sqlcipher_available(),
    reason="sqlcipher3-binary not installed",
)


def _encrypted_task_store(tmp_path: Path) -> tuple[Path, Path]:
    key = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    store = EventStore(db, key_file=key)
    claim = TaskClaim(
        task_id="T-ENC",
        owner="agent/a",
        note="sqlcipher analysis probe",
        claimed_at=10.0,
        lease_expires_at=9999.0,
        status="claimed",
        data_ref="",
        worktree="repo",
        paths=("src/x.py",),
        epoch=1,
        checkpoint="",
    )
    store.append(EventKind.CLAIM, claim.as_dict(), ts=10.0, durable=True)
    store.close()
    return db, key


def test_event_query_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key = _encrypted_task_store(tmp_path)
    code = cli.main(
        [
            "event-query",
            str(db),
            "--db-key-file",
            str(key),
            "task T-ENC timeline",
            "--json",
        ]
    )
    assert code == 0
    assert "T-ENC" in capsys.readouterr().out


def test_merkle_root_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key = _encrypted_task_store(tmp_path)
    code = cli.main(["merkle", "root", str(db), "--db-key-file", str(key), "--json"])
    assert code == 0
    out = capsys.readouterr().out
    assert "root" in out
    assert "tree_size" in out


def test_postmortem_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key = _encrypted_task_store(tmp_path)
    code = cli.main(
        ["postmortem", str(db), "--db-key-file", str(key), "T-ENC", "--json"]
    )
    assert code == 0
    assert "T-ENC" in capsys.readouterr().out
