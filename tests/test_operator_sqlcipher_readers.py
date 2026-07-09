# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — multi-seat operator CLIs open SQLCipher event stores

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.core.approvals import APPROVAL_NOTE_KIND, STATE_REQUESTED, format_approval_note
from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import sqlcipher_available
from synapse_channel.core.state import TaskClaim

pytestmark = pytest.mark.skipif(
    not sqlcipher_available(),
    reason="sqlcipher3-binary not installed",
)


def _encrypted_operator_store(tmp_path: Path) -> tuple[Path, Path]:
    key = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    store = EventStore(db, key_file=key)
    claim = TaskClaim(
        task_id="T-OP",
        owner="agent/a",
        note="operator sqlcipher probe",
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
    note = format_approval_note(
        subject="gate-op", state=STATE_REQUESTED, reason="sqlcipher status probe"
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "author": "agent/a",
            "kind": APPROVAL_NOTE_KIND,
            "task_id": "gate-op",
            "text": note,
        },
        ts=11.0,
        durable=True,
    )
    store.close()
    return db, key


def test_approval_status_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key = _encrypted_operator_store(tmp_path)
    code = cli.main(["approval", "status", str(db), "--db-key-file", str(key), "--json"])
    assert code == 0
    out = capsys.readouterr().out
    assert "gate-op" in out


def test_approval_status_without_key_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, _key = _encrypted_operator_store(tmp_path)
    code = cli.main(["approval", "status", str(db), "--json"])
    assert code != 0
    err = capsys.readouterr().err.lower()
    assert "gate-op" not in err
    assert any(
        token in err
        for token in ("key", "sqlcipher", "encrypt", "cipher", "db-key-file", "database")
    )


def test_trust_graph_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key = _encrypted_operator_store(tmp_path)
    code = cli.main(["trust-graph", str(db), "--db-key-file", str(key), "--json"])
    assert code == 0
    assert capsys.readouterr().out.strip()


def test_trust_graph_without_key_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, _key = _encrypted_operator_store(tmp_path)
    code = cli.main(["trust-graph", str(db), "--json"])
    assert code != 0
    err = capsys.readouterr().err.lower()
    assert "t-op" not in err
    assert any(
        token in err
        for token in ("key", "sqlcipher", "encrypt", "cipher", "db-key-file", "database")
    )


def test_ttl_advice_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key = _encrypted_operator_store(tmp_path)
    code = cli.main(["ttl-advice", str(db), "--db-key-file", str(key), "--json"])
    assert code == 0
    assert capsys.readouterr().out.strip()


def test_ttl_advice_without_key_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, _key = _encrypted_operator_store(tmp_path)
    code = cli.main(["ttl-advice", str(db), "--json"])
    assert code != 0
    err = capsys.readouterr().err.lower()
    assert any(
        token in err
        for token in ("key", "sqlcipher", "encrypt", "cipher", "db-key-file", "database")
    )


def _cross_repo_org(tmp_path: Path) -> Path:
    """Minimal multi-repo tree the cross-repo scanner accepts."""
    root = tmp_path / "org"
    root.mkdir()
    provider = root / "provider"
    provider.mkdir()
    (provider / "pyproject.toml").write_text(
        '[project]\nname = "provider-pkg"\ndependencies = []\n', encoding="utf-8"
    )
    consumer = root / "consumer"
    consumer.mkdir()
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer-pkg"\ndependencies = ["provider-pkg>=1"]\n',
        encoding="utf-8",
    )
    return root


def test_cross_repo_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Shipped ``synapse cross-repo --db --db-key-file`` joins claims from SQLCipher."""
    root = _cross_repo_org(tmp_path)
    db, key = _encrypted_operator_store(tmp_path)
    # Re-open encrypted store and add a claim on a scanned worktree name.
    store = EventStore(db, key_file=key)
    claim = TaskClaim(
        task_id="T-XREPO",
        owner="agent/x",
        note="cross-repo join probe",
        claimed_at=12.0,
        lease_expires_at=9999.0,
        status="claimed",
        data_ref="",
        worktree="consumer",
        paths=("src/y.py",),
        epoch=1,
        checkpoint="",
    )
    store.append(EventKind.CLAIM, claim.as_dict(), ts=12.0, durable=True)
    store.close()
    code = cli.main(
        [
            "cross-repo",
            str(root),
            "--db",
            str(db),
            "--db-key-file",
            str(key),
            "--json",
        ]
    )
    assert code in (0, 1)
    out = capsys.readouterr().out
    assert "T-XREPO" in out or "consumer" in out
    assert "file is not a database" not in out.lower()


def test_cross_repo_without_key_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Plain open of an encrypted hub store via cross-repo must fail closed."""
    root = _cross_repo_org(tmp_path)
    db, _key = _encrypted_operator_store(tmp_path)
    code = cli.main(
        [
            "cross-repo",
            str(root),
            "--db",
            str(db),
            "--json",
        ]
    )
    assert code != 0
    err = capsys.readouterr().err.lower()
    assert "t-xrepo" not in err
    assert any(
        token in err
        for token in ("key", "sqlcipher", "encrypt", "cipher", "db-key-file", "database")
    )


def test_run_cross_repo_graph_key_file_core_path(tmp_path: Path) -> None:
    """Core ``run_cross_repo_graph(..., key_file=)`` opens encrypted store for real."""
    from synapse_channel.core.cross_repo_graph import run_cross_repo_graph

    root = _cross_repo_org(tmp_path)
    db, key = _encrypted_operator_store(tmp_path)
    store = EventStore(db, key_file=key)
    claim = TaskClaim(
        task_id="T-CORE",
        owner="agent/c",
        note="core join",
        claimed_at=13.0,
        lease_expires_at=9999.0,
        status="claimed",
        data_ref="",
        worktree="provider",
        paths=("a.py",),
        epoch=1,
        checkpoint="",
    )
    store.append(EventKind.CLAIM, claim.as_dict(), ts=13.0, durable=True)
    store.close()
    graph = run_cross_repo_graph(root, db_path=db, key_file=key)
    task_ids = {claim.task_id for claim in graph.claims}
    assert "T-CORE" in task_ids


def test_sandbox_attest_writes_encrypted_store(tmp_path: Path) -> None:
    """Shipped ``_attest_run`` opens SQLCipher with key and appends SANDBOX_RUN."""
    from synapse_channel.cli_sandbox import _attest_run
    from synapse_channel.core.sandbox_receipt import RunReceipt

    key = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    # Create encrypted empty store first.
    store = EventStore(db, key_file=key)
    store.close()
    receipt: RunReceipt = {
        "tool_id": "probe",
        "content_digest": "sha256:" + ("a" * 64),
        "inputs_digest": "sha256:" + ("b" * 64),
        "granted_capabilities": [],
        "preopened_paths": [],
        "exit": "ok",
        "output_digest": "sha256:" + ("c" * 64),
        "fuel_used": 1,
        "reason": "",
    }
    _attest_run(db, receipt, key_file=key)
    reopened = EventStore(db, key_file=key)
    try:
        events = list(reopened.read_all())
    finally:
        reopened.close()
    assert any(event.kind == EventKind.SANDBOX_RUN for event in events)


def test_sandbox_attest_without_key_fails_closed(tmp_path: Path) -> None:
    from synapse_channel.cli_sandbox import _attest_run
    from synapse_channel.core.persistence_sqlcipher import SqlCipherKeyError
    from synapse_channel.core.sandbox_receipt import RunReceipt

    key = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    store = EventStore(db, key_file=key)
    store.close()
    receipt: RunReceipt = {
        "tool_id": "probe",
        "content_digest": "sha256:" + ("a" * 64),
        "inputs_digest": "sha256:" + ("b" * 64),
        "granted_capabilities": [],
        "preopened_paths": [],
        "exit": "ok",
        "output_digest": "sha256:" + ("c" * 64),
        "fuel_used": 1,
        "reason": "",
    }
    with pytest.raises((SqlCipherKeyError, ValueError, OSError)):
        _attest_run(db, receipt, key_file=None)
