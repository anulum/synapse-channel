# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — reliability + workflow contention open SQLCipher stores

from __future__ import annotations

import json
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


def _encrypted_store(tmp_path: Path) -> tuple[Path, Path]:
    key = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    store = EventStore(db, key_file=key)
    claim = TaskClaim(
        task_id="step-a",
        owner="agent/a",
        note="reliability sqlcipher probe",
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


def _workflow_file(tmp_path: Path) -> Path:
    path = tmp_path / "wf.json"
    path.write_text(
        json.dumps(
            {
                "name": "sqlcipher-wf",
                "steps": [
                    {"id": "step-a", "title": "A"},
                    {"id": "step-b", "title": "B", "depends_on": ["step-a"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _assert_fail_closed(code: int, err: str) -> None:
    assert code != 0
    text = err.lower()
    assert "step-a" not in text or any(
        token in text for token in ("key", "sqlcipher", "cipher", "database")
    )
    assert any(
        token in text
        for token in ("key", "sqlcipher", "encrypt", "cipher", "db-key-file", "database")
    )


def test_reliability_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key = _encrypted_store(tmp_path)
    code = cli.main(["reliability", str(db), "--db-key-file", str(key), "--json"])
    assert code == 0
    out = capsys.readouterr().out
    assert out.strip()
    assert "file is not a database" not in out.lower()


def test_reliability_without_key_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, _key = _encrypted_store(tmp_path)
    code = cli.main(["reliability", str(db), "--json"])
    _assert_fail_closed(code, capsys.readouterr().err)


def test_reliability_wrong_key_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, _key = _encrypted_store(tmp_path)
    wrong = generate_key_file(tmp_path / "wrong.key")
    code = cli.main(["reliability", str(db), "--db-key-file", str(wrong), "--json"])
    _assert_fail_closed(code, capsys.readouterr().err)


def test_workflow_contention_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key = _encrypted_store(tmp_path)
    wf = _workflow_file(tmp_path)
    code = cli.main(
        [
            "workflow",
            "contention",
            str(wf),
            str(db),
            "--db-key-file",
            str(key),
            "--json",
        ]
    )
    # 0 = no overlaps, 1 = overlaps; both mean store opened successfully.
    assert code in (0, 1)
    captured = capsys.readouterr()
    assert "file is not a database" not in captured.out.lower()
    assert "could not read" not in captured.err.lower()


def test_workflow_contention_without_key_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, _key = _encrypted_store(tmp_path)
    wf = _workflow_file(tmp_path)
    code = cli.main(["workflow", "contention", str(wf), str(db), "--json"])
    _assert_fail_closed(code, capsys.readouterr().err)


def test_workflow_contention_wrong_key_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, _key = _encrypted_store(tmp_path)
    wrong = generate_key_file(tmp_path / "wrong-wf.key")
    wf = _workflow_file(tmp_path)
    code = cli.main(
        [
            "workflow",
            "contention",
            str(wf),
            str(db),
            "--db-key-file",
            str(wrong),
            "--json",
        ]
    )
    _assert_fail_closed(code, capsys.readouterr().err)


def test_causality_contention_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sibling path already wired; confirm SQLCipher still succeeds end-to-end."""
    db, key = _encrypted_store(tmp_path)
    code = cli.main(
        [
            "causality",
            "contention",
            str(db),
            "--db-key-file",
            str(key),
            "--json",
        ]
    )
    assert code in (0, 1)
    assert "file is not a database" not in capsys.readouterr().out.lower()
