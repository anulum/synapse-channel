# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — local wake storage authority contracts
"""Qualify owner controls against changed policies, raw corruption and stale intents."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from synapse_channel.core.secure_path import apply_owner_only_file
from synapse_channel.durable_dir import private_dir
from synapse_channel.mcp.http_config import load_http_auth_config
from synapse_channel.mcp.wake_admission import WakeAdmission
from synapse_channel.mcp.wake_ledger import WakeLedger
from synapse_channel.mcp.wake_limits import ROOT_CATALOGUE, ROOT_LOCK
from synapse_channel.mcp.wake_pointer import WakeLedgerError
from synapse_channel.mcp.wake_storage_control import WakeStorageControl
from test_mcp_wake_control import operator_ledger
from test_mcp_wake_root_archive import archive, inactive_generation, interrupt_archive


def test_disabled_subject_generation_can_be_archived_without_native_processing(
    tmp_path: Path,
) -> None:
    path, old, _ = operator_ledger(tmp_path)
    policy = json.loads(path.read_text())
    policy["subjects"]["alice"]["enabled"] = False
    path.write_text(json.dumps(policy))
    before = old.path.read_bytes()
    control = WakeStorageControl(path)
    result = archive(control, control.inspect(old.scope.key), tmp_path / "archive")
    assert result["outcome"] == "archived" and not old.path.exists()
    assert (Path(result["archive_directory"]) / old.path.name).read_bytes() == before


@pytest.mark.parametrize(
    "damage", ["application", "missing-meta", "pending", "schema", "state", "population"]
)
def test_raw_native_corruption_is_unknown_not_an_empty_or_consumed_queue(
    tmp_path: Path, damage: str
) -> None:
    _, old, control, _ = inactive_generation(tmp_path)
    with sqlite3.connect(old.path) as database:
        if damage == "application":
            database.execute("PRAGMA application_id=1")
        elif damage == "missing-meta":
            database.execute("DELETE FROM meta")
        elif damage == "pending":
            database.execute("UPDATE meta SET pending=8")
        elif damage == "schema":
            database.execute("PRAGMA user_version=99")
        elif damage == "state":
            database.execute("UPDATE deliveries SET state='unknown'")
        else:
            database.executemany(
                "INSERT INTO deliveries(id,revision,bits,body,state,attempt,deadline,due) "
                "SELECT id||?,revision,bits,body,state,attempt,deadline,due "
                "FROM deliveries WHERE ordinal=1",
                [(str(number),) for number in range(64)],
            )
    before = old.path.read_bytes()
    info = control.inspect(old.scope.key)
    assert info["transport"] is None
    assert info["files"] == [
        {
            "name": old.path.name,
            "bytes": len(before),
            "sha256": hashlib.sha256(before).hexdigest(),
        }
    ]
    assert old.path.read_bytes() == before


def test_retired_manifest_binding_change_refuses_repeat_without_overwriting_archive(
    tmp_path: Path,
) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    result = archive(control, info, tmp_path / "archive")
    directory = Path(result["archive_directory"])
    before = (directory / old.path.name).read_bytes()
    manifest = directory / "manifest.json"
    value = json.loads(manifest.read_text())
    value["scope_key"] = "f" * 64
    value["files"][0]["name"] = "f" * 64 + ".sqlite3"
    manifest.write_text(json.dumps(value))
    changed = manifest.read_bytes()
    with pytest.raises(WakeLedgerError, match="manifest binding changed"):
        archive(control, info, tmp_path / "archive")
    assert manifest.read_bytes() == changed
    assert (directory / old.path.name).read_bytes() == before


@pytest.mark.parametrize("case", ["policy", "active", "missing"])
def test_cancel_refuses_stale_authority_or_absent_intent_without_changing_native_work(
    tmp_path: Path, case: str
) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    before = old.path.read_bytes()
    if case == "policy":
        info["policy_digest"] = "0" * 64
    elif case == "missing":
        info["key"] = "f" * 64
    with pytest.raises(WakeLedgerError):
        control.cancel(info["key"], info["fingerprint"], info["policy_digest"])
    assert old.path.read_bytes() == before
    assert control.inspect(old.scope.key)["phase"] == "active"


@pytest.mark.parametrize("case", ["changed-source", "stale-snapshot"])
def test_cancel_cannot_discard_a_new_source_or_an_exact_snapshot_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    partial = interrupt_archive(control, info, tmp_path / "archive", monkeypatch)
    copied = partial.read_bytes()
    if case == "changed-source":
        with sqlite3.connect(old.path) as database:
            database.execute("UPDATE meta SET pending=7")
    else:
        info["fingerprint"] = "0" * 64
    before = old.path.read_bytes()
    with pytest.raises(WakeLedgerError, match="source changed"):
        control.cancel(info["key"], info["fingerprint"], info["policy_digest"])
    assert old.path.read_bytes() == before and partial.read_bytes() == copied
    assert control.inspect(old.scope.key)["phase"] == "archiving"


def test_rejected_first_legacy_archive_does_not_poison_root_initialization(tmp_path: Path) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    with sqlite3.connect(old.path) as database:
        database.execute("PRAGMA user_version=3")
    before = old.path.read_bytes()
    for name in (ROOT_CATALOGUE, ROOT_LOCK):
        (old.path.parent / name).unlink()
    with pytest.raises(WakeLedgerError, match="policy changed"):
        control.archive(old.scope.key, info["fingerprint"], "0" * 64, tmp_path / "archive", 100)
    assert old.path.read_bytes() == before
    assert control.status()["revision"] == 0
    fresh = control.inspect(old.scope.key)
    assert archive(control, fresh, tmp_path / "archive")["outcome"] == "archived"


def test_another_policy_file_cannot_archive_a_current_generation_in_the_same_directory(
    tmp_path: Path,
) -> None:
    active_file, current, _ = operator_ledger(tmp_path)
    active = WakeStorageControl(active_file)
    info = active.inspect(current.scope.key)
    fingerprint = info["fingerprint"]
    assert isinstance(fingerprint, str)
    before = current.path.read_bytes()
    policy = json.loads(active_file.read_text())
    policy["subjects"]["alice"]["projects"]["ALPHA"]["wake"]["url"] += "/another-policy"
    other_file = tmp_path / "other.json"
    other_file.write_text(json.dumps(policy))
    apply_owner_only_file(other_file)
    probe_directory = tmp_path / "probe"
    private_dir(probe_directory, purpose="independent policy probe")
    probe_file = probe_directory / other_file.name
    probe_file.write_bytes(other_file.read_bytes())
    apply_owner_only_file(probe_file)
    digest = WakeStorageControl(probe_file).configure(128, 512 * 1024 * 1024, 0)["policy_digest"]
    assert isinstance(digest, str)
    with pytest.raises(WakeLedgerError, match="another policy"):
        WakeStorageControl(other_file).archive(
            current.scope.key, fingerprint, digest, tmp_path / "archive", 100
        )
    assert current.path.read_bytes() == before
    assert current.summary()["unresolved_failures"] == 1


def test_another_native_policy_cannot_open_a_shared_root_but_uses_its_own_directory(
    tmp_path: Path,
) -> None:
    active_file, current, _ = operator_ledger(tmp_path)
    before = current.path.read_bytes()
    other = tmp_path / "other.json"
    other.write_bytes(active_file.read_bytes())
    apply_owner_only_file(other)
    admission = WakeAdmission(other, "alice", "ALPHA", load_http_auth_config(other))
    denied = WakeLedger(current.path.parent, admission.scope("operator-hub"))
    assert denied.scope.key == current.scope.key
    with pytest.raises(WakeLedgerError, match="another policy"):
        denied.observe_page(1, 2, sources=("tasks",))
    assert current.path.read_bytes() == before
    assert current.summary()["unresolved_failures"] == 1
    independent = tmp_path / "independent"
    private_dir(independent, purpose="independent MCP policy")
    separate = independent / other.name
    separate.write_bytes(other.read_bytes())
    apply_owner_only_file(separate)
    admitted = WakeAdmission(separate, "alice", "ALPHA", load_http_auth_config(separate))
    working = WakeLedger(independent / ".synapse-mcp-wake", admitted.scope("operator-hub"))
    assert working.observe_page(0, 2, sources=("tasks",))
    assert working.summary()["pending_sources"] == 2
