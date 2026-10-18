# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — native adapter transaction recovery
"""Recover real on-disk prepared transactions through the public installer."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from synapse_channel.adapter_custody import manage_adapter
from synapse_channel.adapter_custody_files import AdapterCustodyError
from synapse_channel.adapter_custody_store import CustodyStore, Ownership, PendingUpdate
from synapse_channel.adapters import render_block, tool_for
from synapse_channel.advisory_lock import locked
from synapse_channel.cli import main


def prepared(root: Path) -> tuple[Path, Path, Ownership]:
    """Commit native intent with the same public store API used by the installer."""
    tool = tool_for("codex")
    target = root / tool.target
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original\r\n")
    directory = target.parent / f".{target.name}.synapse-custody"
    directory.mkdir(mode=0o700)
    journal = directory / "custody.sqlite3"
    block = render_block(tool, identity="new/agent", hub_uri="ws://localhost:8876")
    following = Ownership(tool.key, tool.mode, "original\r\n", block)
    with CustodyStore(journal, target) as store:
        store.save(None, PendingUpdate("original\r\n", block, following))
    return target, journal, following


@pytest.mark.parametrize("effect_applied", [False, True])
def test_recovery_settles_observed_bytes_without_replaying_writes(
    tmp_path: Path, effect_applied: bool
) -> None:
    """Both sides of the effect/settlement crash boundary have a safe outcome."""
    target, journal, following = prepared(tmp_path)
    if effect_applied:
        target.write_bytes(following.owned.encode())
    # A killed writer can leave a partial UTF-8 sequence in its private stage.
    stage = journal.parent / "replacement"
    stage.write_bytes(b"partial \xe2")
    stage.chmod(0o600)
    before = target.read_bytes()
    assert manage_adapter(tool_for("codex"), root=tmp_path, action="recover") == "recovered"
    assert target.read_bytes() == before
    assert not stage.exists()
    with CustodyStore(journal, target, readonly=True) as store:
        owned, pending = store.load()
    assert pending is None
    assert owned == (following if effect_applied else None)
    if effect_applied:
        assert manage_adapter(tool_for("codex"), root=tmp_path, action="uninstall") == "restored"
        assert target.read_bytes() == b"original\r\n"


def test_recovery_preserves_conflicting_target_and_pending_evidence(tmp_path: Path) -> None:
    """A third party's bytes never become rollback input or an automatic overwrite."""
    target, journal, _following = prepared(tmp_path)
    target.write_bytes(b"third party changed this\r\n")
    before = journal.read_bytes()
    result = main(["adapters", "recover", "codex", "--home", str(tmp_path)])
    assert result == 2
    assert target.read_bytes() == b"third party changed this\r\n"
    assert journal.read_bytes() == before


def test_preview_refuses_pending_intent_without_settling_it(tmp_path: Path) -> None:
    """Dry run cannot silently mutate recovery state."""
    target, journal, following = prepared(tmp_path)
    before = journal.read_bytes()
    with pytest.raises(AdapterCustodyError, match="requires recovery"):
        manage_adapter(
            tool_for("codex"), root=tmp_path, action="install", block=following.owned, dry_run=True
        )
    assert journal.read_bytes() == before
    assert target.read_bytes() == b"original\r\n"


def test_two_cli_processes_respect_one_held_target_lock(tmp_path: Path) -> None:
    """A contender refuses immediately; process exit releases the native lock."""
    tool = tool_for("codex")
    block = render_block(tool, identity="owner/agent", hub_uri="ws://localhost:8876")
    assert manage_adapter(tool, root=tmp_path, action="install", block=block) == "installed"
    target = tmp_path / tool.target
    directory = target.parent / f".{target.name}.synapse-custody"
    argv = [
        sys.executable,
        "-m",
        "synapse_channel.cli",
        "adapters",
        "uninstall",
        "codex",
        "--home",
        str(tmp_path),
        "--project",
        str(tmp_path),
    ]
    with locked(directory / "lock", blocking=False) as acquired:
        assert acquired
        for _attempt in range(2):
            result = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
            assert result.returncode == 2
            assert "Another installer" in result.stderr
            assert target.read_bytes() == block.encode()
    result = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    assert not target.exists()


@pytest.mark.parametrize(
    "damage",
    [
        "digest",
        "json",
        "schema",
        "target",
        "keys",
        "duplicate",
        "mode",
        "pending",
        "ownership",
        "empty-owned",
        "before",
    ],
)
def test_corrupt_journal_refuses_target_mutation(tmp_path: Path, damage: str) -> None:
    """Changed or malformed ownership records remain unusable even with valid markers."""
    target, journal, _following = prepared(tmp_path)
    with sqlite3.connect(journal) as connection:
        row = connection.execute("SELECT document FROM custody WHERE id=1").fetchone()
        assert row is not None
        raw = str(row[0])
        value = json.loads(raw)
        if damage == "json":
            raw = "{"
        elif damage == "duplicate":
            raw = raw.replace('"schema":', '"schema":"duplicate","schema":', 1)
        elif damage != "digest":
            if damage == "schema":
                value["schema"] = "other-version"
            elif damage == "target":
                value["target"] = "/another/target"
            elif damage == "keys":
                value["unexpected"] = True
            elif damage == "mode":
                value["pending"]["ownership"]["mode"] = []
            elif damage == "ownership":
                value["pending"]["ownership"] = {"tool": "partial"}
            elif damage == "empty-owned":
                value["pending"]["ownership"]["owned"] = ""
            elif damage == "before":
                value["pending"]["before"] = True
            else:
                value["pending"] = {"before": "unclosed"}
            raw = json.dumps(value)
        digest = "changed" if damage == "digest" else hashlib.sha256(raw.encode()).hexdigest()
        connection.execute("UPDATE custody SET document=?,sha256=?", (raw, digest))
    original = target.read_bytes()
    assert main(["adapters", "recover", "codex", "--home", str(tmp_path)]) == 2
    assert target.read_bytes() == original


def test_unrecorded_stage_requires_inspection(tmp_path: Path) -> None:
    """A scratch entry without prepared intent is not automatically discarded."""
    tool = tool_for("codex")
    block = render_block(tool, identity="owner", hub_uri="ws://localhost:8876")
    manage_adapter(tool, root=tmp_path, action="install", block=block)
    stage = tmp_path / ".codex/.synapse.md.synapse-custody/replacement"
    stage.write_bytes(b"unknown owner data")
    with pytest.raises(AdapterCustodyError, match="Unrecorded"):
        manage_adapter(tool, root=tmp_path, action="recover")
    assert stage.read_bytes() == b"unknown owner data"


@pytest.mark.parametrize("damage", ["unsupported-version", "missing-row", "oversized-row"])
def test_incomplete_native_database_is_refused(tmp_path: Path, damage: str) -> None:
    """An incomplete database never silently initializes over prior ownership."""
    target, journal, _following = prepared(tmp_path)
    with sqlite3.connect(journal) as connection:
        if damage == "unsupported-version":
            connection.execute("PRAGMA user_version=0")
        elif damage == "missing-row":
            connection.execute("DELETE FROM custody")
        else:
            connection.execute("UPDATE custody SET document=zeroblob(?)", (33 * 1_048_576,))
    before = target.read_bytes()
    assert main(["adapters", "recover", "codex", "--home", str(tmp_path)]) == 2
    assert target.read_bytes() == before


def test_store_refuses_excessive_prepared_document_without_changing_current_state(
    tmp_path: Path,
) -> None:
    """JSON escaping cannot bypass the prepared-document memory/storage ceiling."""
    target, journal, _following = prepared(tmp_path)
    text = "\x00" * 1_048_576
    following = Ownership("codex", "file", text, text)
    with CustodyStore(journal, target) as store:
        before = store.load()
        with pytest.raises(AdapterCustodyError, match="exceeds its bound"):
            store.save(following, PendingUpdate(text, text, following))
        assert store.load() == before


@pytest.mark.parametrize("phase", ["prepared", "applied"])
def test_native_writer_death_releases_lock_and_preserves_recoverable_intent(
    tmp_path: Path, phase: str
) -> None:
    """Kill an owned finite writer after committed intent or actual file replacement."""
    code = """
import sys
from pathlib import Path
from synapse_channel.adapter_custody_store import CustodyStore, Ownership, PendingUpdate
from synapse_channel.adapter_custody_files import replace_target
from synapse_channel.adapters import tool_for, render_block
from synapse_channel.advisory_lock import locked
from synapse_channel.opencode_adapter_files import read_text_snapshot
root = Path(sys.argv[1])
tool = tool_for("codex")
target = root / tool.target
target.parent.mkdir()
target.write_bytes(b"original\\r\\n")
directory = target.parent / ".synapse.md.synapse-custody"
block = render_block(tool, identity="writer", hub_uri="ws://localhost:8876")
following = Ownership(tool.key, tool.mode, "original\\r\\n", block)
with locked(directory / "lock", blocking=False) as acquired:
    assert acquired
    with CustodyStore(directory / "custody.sqlite3", target) as store:
        store.save(None, PendingUpdate("original\\r\\n", block, following))
        if sys.argv[2] == "applied":
            replace_target(target, block, read_text_snapshot(target), directory=directory)
        print("READY", flush=True)
        (root / "ready").write_text("READY", encoding="utf-8")
        sys.stdin.buffer.read(1)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path), phase],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while not (tmp_path / "ready").exists():
            assert process.poll() is None, "Writer exited before preparing its transaction"
            assert time.monotonic() < deadline, "Writer did not reach the finite test checkpoint"
            time.sleep(0.01)
        process.kill()
        _stdout, stderr = process.communicate(timeout=10)
        assert not stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    before = (tmp_path / ".codex/synapse.md").read_bytes()
    assert manage_adapter(tool_for("codex"), root=tmp_path, action="recover") == "recovered"
    assert (tmp_path / ".codex/synapse.md").read_bytes() == before
