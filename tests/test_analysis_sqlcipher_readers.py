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


def test_causality_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key = _encrypted_task_store(tmp_path)
    # Keep positionals contiguous; flags after SEQ (argparse interleaving).
    code = cli.main(
        [
            "causality",
            "causes",
            str(db),
            "1",
            "--db-key-file",
            str(key),
            "--json",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "T-ENC" in out
    assert '"present": true' in out


def test_causality_health_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key = _encrypted_task_store(tmp_path)
    code = cli.main(
        ["causality", "health", str(db), "--db-key-file", str(key), "--json"]
    )
    # Anomalies (orphan claim) exit 1; open of encrypted store still succeeded.
    assert code in (0, 1)
    out = capsys.readouterr().out
    assert "T-ENC" in out
    assert "anomaly_count" in out


def test_accounting_report_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key = _encrypted_task_store(tmp_path)
    code = cli.main(
        ["accounting", "report", str(db), "--db-key-file", str(key), "--json"]
    )
    assert code == 0
    assert capsys.readouterr().out.strip()


def test_memory_recall_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key = _encrypted_task_store(tmp_path)
    code = cli.main(
        [
            "memory-recall",
            str(db),
            "--db-key-file",
            str(key),
            "sqlcipher",
            "--json",
        ]
    )
    # Reader must open the encrypted store; empty recall is still success path.
    captured = capsys.readouterr()
    assert "file is not a database" not in captured.err.lower()
    assert "not a database" not in captured.err.lower()
    assert code != 2 or "missing event store" not in captured.err.lower()


def test_reproduce_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key = _encrypted_task_store(tmp_path)
    code = cli.main(
        ["reproduce", str(db), "--db-key-file", str(key), "T-ENC", "--json"]
    )
    assert code == 0
    assert "T-ENC" in capsys.readouterr().out


def test_debug_fork_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key = _encrypted_task_store(tmp_path)
    code = cli.main(
        [
            "debug",
            str(db),
            "--fork-at",
            "1",
            "--db-key-file",
            str(key),
            "--json",
        ]
    )
    assert code in (0, 1)
    out = capsys.readouterr().out
    assert "T-ENC" in out or "task_id" in out
