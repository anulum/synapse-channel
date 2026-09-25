# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from synapse_channel.core import protected_write_inspection as inspection
from synapse_channel.core.protected_write_inspection import (
    inspect_protected_file,
    verify_protected_auxiliary_before_states,
    verify_protected_primary_before_states,
)
from test_protected_write_proposal import LIMITS, _proposal

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor backend tests")


def _directory_inspection(root: Path, path: str) -> inspection.ProtectedFileInspection:
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        identity = os.fstat(descriptor)
        return inspect_protected_file(
            descriptor,
            path,
            root_identity=(identity.st_dev, identity.st_ino),
            max_bytes=64,
            allow_directory=True,
        )
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("planned", [False, True])
def test_auxiliary_directory_and_child_absence_proof(tmp_path: Path, planned: bool) -> None:
    proposal = cast(dict[str, Any], _proposal())
    directory = {"kind": "directory", "mode": "0700"}
    if not planned:
        (tmp_path / "records").mkdir(mode=0o700)
    parent = _directory_inspection(tmp_path, "records")
    observed = {("memory", "records"): parent}
    if not planned:
        observed[("memory", "records/note.md")] = _inspect(tmp_path, "records/note.md")
    else:
        proposal["auxiliary_operations"].insert(
            0,
            {
                "operation_id": "mkdir-records",
                "opcode": "mkdir",
                "content_reference": None,
                "paths": [
                    {
                        "root_id": "memory",
                        "relative_path": "records",
                        "before": {"kind": "absent"},
                        "after": directory,
                    }
                ],
            },
        )
    proposal["auxiliary_operations"].append(
        {
            "operation_id": "flush-parent",
            "opcode": "fsync",
            "content_reference": None,
            "paths": [
                {
                    "root_id": "memory",
                    "relative_path": "records",
                    "before": directory,
                    "after": directory,
                }
            ],
        }
    )
    roots = {"memory": parent.directories[0]}
    keys = verify_protected_auxiliary_before_states(
        json.dumps(proposal), limits=LIMITS, inspections=observed, enrolled_roots=roots
    )
    assert set(keys) == {("memory", "records"), ("memory", "records/note.md")}
    if planned:
        nested = cast(dict[str, Any], json.loads(json.dumps(proposal)))
        nested["operations"][0]["relative_path"] = "records/missing/note.md"
        nested["auxiliary_operations"][1]["paths"][0]["relative_path"] = "records/missing/note.md"
        with pytest.raises(ValueError, match="absence proof"):
            verify_protected_auxiliary_before_states(
                json.dumps(nested), limits=LIMITS, inspections=observed, enrolled_roots=roots
            )
    if not planned:
        assert parent.is_directory and parent.sha256 is None
        with pytest.raises(ValueError, match="absence proof"):
            verify_protected_auxiliary_before_states(
                json.dumps(proposal),
                limits=LIMITS,
                inspections={("memory", "records"): parent},
                enrolled_roots=roots,
            )
    with pytest.raises(ValueError, match="root"):
        verify_protected_auxiliary_before_states(
            json.dumps(proposal), limits=LIMITS, inspections=observed, enrolled_roots={}
        )
    mismatched = {**observed, ("memory", "records"): replace(parent, relative_path="wrong")}
    with pytest.raises(ValueError, match="mismatch"):
        verify_protected_auxiliary_before_states(
            json.dumps(proposal), limits=LIMITS, inspections=mismatched, enrolled_roots=roots
        )


def test_directory_option_and_incomplete_file_observation_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="directory inspection option"):
        inspect_protected_file(
            -1, "record", root_identity=(0, 0), max_bytes=64, allow_directory=cast(bool, 1)
        )
    (tmp_path / "records").mkdir()
    observed = _inspect(tmp_path, "records/note.md")
    invalid = replace(observed, file_identity=(1, 1), mode=0o600, sha256=None)
    with pytest.raises(ValueError, match="incomplete file"):
        verify_protected_primary_before_states(
            json.dumps(_proposal()),
            limits=LIMITS,
            inspections={("memory", "records/note.md"): invalid},
            enrolled_roots={"memory": observed.directories[0]},
        )


def test_directory_device_mismatch_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "records").mkdir()
    original = os.fstat
    calls = 0

    def fstat(descriptor: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        result = original(descriptor)
        if calls == 3:
            fields = list(result)
            fields[2] += 1
            return os.stat_result(fields)
        return result

    # Injected identity/device mismatch checks refusal; activation needs a real competing path or
    # mount change in the sandbox.
    monkeypatch.setattr(os, "fstat", fstat)
    with pytest.raises(ValueError, match="directory crosses"):
        _directory_inspection(tmp_path, "records")


@pytest.mark.parametrize("identity", [(True, 1), (1.0, 1), (-1, 1), (1,), [1, 2], None])
def test_pinned_identity_requires_exact_integer_pair(identity: object) -> None:
    with pytest.raises(ValueError, match="pinned root identity"):
        inspect_protected_file(-1, "record", root_identity=identity, max_bytes=64)  # type: ignore[arg-type]


def test_primary_before_binding_uses_real_empty_file_and_absent_target(tmp_path: Path) -> None:
    proposal = cast(dict[str, Any], _proposal())
    (tmp_path / "records").mkdir()
    observation = _inspect(tmp_path, "records/note.md")
    roots = {"memory": observation.directories[0]}
    assert verify_protected_primary_before_states(
        json.dumps(proposal),
        limits=LIMITS,
        inspections={("memory", "records/note.md"): observation},
        enrolled_roots=roots,
    ) == (observation,)
    target = tmp_path / "records" / "note.md"
    target.write_bytes(b"")
    target.chmod(0o600)
    observation = _inspect(tmp_path, "records/note.md")
    assert observation.size_bytes == 0
    assert observation.sha256 == hashlib.sha256(b"").hexdigest()
    with pytest.raises(ValueError, match="before state"):
        verify_protected_primary_before_states(
            json.dumps(proposal),
            limits=LIMITS,
            inspections={("memory", "records/note.md"): observation},
            enrolled_roots=roots,
        )
    before = {"kind": "file", "sha256": observation.sha256, "size_bytes": 0, "mode": "0600"}
    proposal["operations"][0].update(action="replace", before=before)
    proposal["auxiliary_operations"][0].update(opcode="write")
    proposal["auxiliary_operations"][0]["paths"][0]["before"] = before
    assert verify_protected_primary_before_states(
        json.dumps(proposal),
        limits=LIMITS,
        inspections={("memory", "records/note.md"): observation},
        enrolled_roots=roots,
    ) == (observation,)


@pytest.mark.parametrize("case", ["missing", "root", "path", "incomplete"])
def test_primary_before_binding_rejects_misrouted_observations(tmp_path: Path, case: str) -> None:
    (tmp_path / "records").mkdir()
    proposal = _proposal()
    observed = _inspect(tmp_path, "records/note.md")
    roots = {"memory": observed.directories[0]}
    if case == "root":
        roots.clear()
    elif case == "path":
        observed = replace(observed, relative_path="records/other")
    elif case == "incomplete":
        observed = replace(observed, file_identity=(1, 1), mode=None)
    inspections = {} if case == "missing" else {("memory", "records/note.md"): observed}
    with pytest.raises(ValueError):
        verify_protected_primary_before_states(
            json.dumps(proposal), limits=LIMITS, inspections=inspections, enrolled_roots=roots
        )


def _inspect(
    root: Path, path: str = "record", budget: int = 64
) -> inspection.ProtectedFileInspection:
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        info = os.fstat(descriptor)
        result = inspect_protected_file(
            descriptor, path, root_identity=(info.st_dev, info.st_ino), max_bytes=budget
        )
        assert os.fstat(descriptor).st_ino == info.st_ino
        return result
    finally:
        os.close(descriptor)


def test_real_bytes_absence_and_borrowed_root_descriptor(tmp_path: Path) -> None:
    (tmp_path / "records").mkdir()
    target = tmp_path / "records" / "record"
    target.write_bytes(b"retained bytes")
    result = _inspect(tmp_path, "records/record")
    assert result.sha256 == hashlib.sha256(b"retained bytes").hexdigest()
    assert result.size_bytes == 14
    assert result.file_identity == (target.stat().st_dev, target.stat().st_ino)
    assert len(result.directories) == 2
    assert _inspect(tmp_path, "records/absent").file_identity is None
    with pytest.raises(FileNotFoundError):
        _inspect(tmp_path, "absent/record")


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/record",
        "../record",
        "./record",
        "a//b",
        "a/../b",
        "a\\b",
        "a*",
        "a?",
        "a[0]",
        "a\x00",
        "a\x7f",
    ],
)
def test_inspection_refuses_unsafe_path(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValueError, match="relative path"):
        _inspect(tmp_path, path)


@pytest.mark.parametrize("budget", [True, 0, -1, 1.0, 2**53])
def test_inspection_refuses_invalid_budget(tmp_path: Path, budget: int) -> None:
    with pytest.raises(ValueError, match="budget"):
        _inspect(tmp_path, budget=budget)


@pytest.mark.parametrize(
    "kind", ["leaf-link", "parent-link", "hardlink", "fifo", "directory", "oversize"]
)
def test_real_filesystem_aliases_and_unsafe_objects_are_refused(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "record"
    if kind == "leaf-link":
        (tmp_path / "source").write_bytes(b"x")
        target.symlink_to("source")
    elif kind == "parent-link":
        (tmp_path / "source").mkdir()
        target.symlink_to("source", target_is_directory=True)
    elif kind == "hardlink":
        target.write_bytes(b"x")
        os.link(target, tmp_path / "alias")
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "directory":
        target.mkdir()
    else:
        target.write_bytes(b"x" * 65)
    with pytest.raises((OSError, ValueError)):
        _inspect(tmp_path, "record/child" if kind == "parent-link" else "record")


def test_wrong_pinned_root_refused(tmp_path: Path) -> None:
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError, match="root identity"):
            inspect_protected_file(descriptor, "record", root_identity=(0, 0), max_bytes=64)
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("change", ["grow", "same-size"])
def test_real_concurrent_file_change_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    target = tmp_path / "record"
    target.write_bytes(b"old")
    original_read = os.read
    changed = False

    def read(descriptor: int, count: int) -> bytes:
        nonlocal changed
        result = original_read(descriptor, count)
        if not changed:
            changed = True
            target.write_bytes(b"larger" if change == "grow" else b"new")
            os.utime(target, ns=(1, 1))
        return result

    # Injected growth checks the bound; activation needs a second process growing the actual file
    # during inspection.
    monkeypatch.setattr(os, "read", read)
    with pytest.raises(ValueError, match="grew|changed"):
        _inspect(tmp_path, budget=3)


def test_unsupported_backend_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Injected platform refusal checks fallback; a real non-POSIX runner must verify backend
    # behavior.
    monkeypatch.setattr(inspection, "os", SimpleNamespace(name="unsupported"))
    with pytest.raises(OSError, match="unavailable"):
        inspect_protected_file(0, "record", root_identity=(1, 1), max_bytes=64)


def test_changed_device_metadata_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "records").mkdir()
    original = os.fstat
    calls = 0

    def fstat(descriptor: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        result = original(descriptor)
        if calls == 3:  # wrapper root, inspection root, then child directory
            fields = list(result)
            fields[2] += 1
            return os.stat_result(fields)
        return result

    # Injected identity/device mismatch checks refusal; activation needs a real competing path or
    # mount change in the sandbox.
    monkeypatch.setattr(os, "fstat", fstat)
    with pytest.raises(ValueError, match="device boundary"):
        _inspect(tmp_path, "records/record")
