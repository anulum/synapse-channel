# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — sandbox host-path hardening: canonicalise, refuse a symlink escape

from __future__ import annotations

import os
from pathlib import Path

import pytest

from synapse_channel.core.sandbox_paths import (
    PreopenCheck,
    SandboxPathError,
    check_preopen_host,
    harden_preopens,
    resolve_preopen_host,
)
from synapse_channel.core.sandbox_policy import (
    CapabilityManifest,
    FilesystemGrant,
    ResourceGrant,
)
from synapse_channel.core.sandbox_receipt import EXIT_ERROR
from synapse_channel.core.wasm_sandbox import run_sandboxed


def test_a_real_directory_resolves_to_its_canonical_path(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    resolved = resolve_preopen_host(str(work))
    assert resolved == os.path.realpath(work)
    assert os.path.isabs(resolved)


def test_a_directory_that_is_a_symlink_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(SandboxPathError, match="symlink"):
        resolve_preopen_host(str(link))


def test_a_symlinked_parent_component_is_refused(tmp_path: Path) -> None:
    # The grant names a plain child, but its parent is a symlink that redirects the path —
    # resolving it reaches a directory the operator did not literally grant.
    real_parent = tmp_path / "real_parent"
    (real_parent / "child").mkdir(parents=True)
    linked_parent = tmp_path / "linked_parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(SandboxPathError, match="symlink"):
        resolve_preopen_host(str(linked_parent / "child"))


def test_a_missing_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SandboxPathError, match="not an existing directory"):
        resolve_preopen_host(str(tmp_path / "does-not-exist"))


def test_a_file_is_refused(tmp_path: Path) -> None:
    a_file = tmp_path / "a_file"
    a_file.write_text("x", encoding="utf-8")
    with pytest.raises(SandboxPathError, match="not an existing directory"):
        resolve_preopen_host(str(a_file))


def test_harden_preopens_resolves_each_host_and_preserves_guest_and_write(tmp_path: Path) -> None:
    read_dir = tmp_path / "in"
    read_dir.mkdir()
    write_dir = tmp_path / "out"
    write_dir.mkdir()
    hardened = harden_preopens(((str(read_dir), "/in", False), (str(write_dir), "/out", True)))
    assert hardened == (
        (os.path.realpath(read_dir), "/in", False),
        (os.path.realpath(write_dir), "/out", True),
    )


def test_harden_preopens_refuses_the_whole_set_on_one_bad_host(tmp_path: Path) -> None:
    good = tmp_path / "good"
    good.mkdir()
    link = tmp_path / "link"
    link.symlink_to(good, target_is_directory=True)
    with pytest.raises(SandboxPathError):
        harden_preopens(((str(good), "/g", False), (str(link), "/l", False)))


def test_check_preopen_host_reports_a_real_directory_as_ok(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    check = check_preopen_host(str(work))
    assert check == PreopenCheck(
        host_path=str(work), ok=True, resolved=os.path.realpath(work), reason=""
    )


def test_check_preopen_host_reports_a_symlink_as_refused_without_raising(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    check = check_preopen_host(str(link))
    assert check.ok is False
    assert check.resolved == ""
    assert "symlink" in check.reason
    assert check.host_path == str(link)


def test_check_preopen_host_reports_a_missing_directory_as_refused(tmp_path: Path) -> None:
    check = check_preopen_host(str(tmp_path / "gone"))
    assert check.ok is False
    assert "not an existing directory" in check.reason


def test_a_path_under_an_approved_root_resolves(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    inside = root / "in"
    inside.mkdir(parents=True)
    assert resolve_preopen_host(str(inside), approved_roots=[str(root)]) == os.path.realpath(inside)


def test_the_approved_root_itself_resolves(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    assert resolve_preopen_host(str(root), approved_roots=[str(root)]) == os.path.realpath(root)


def test_a_path_outside_every_approved_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(SandboxPathError, match="outside the approved workspace"):
        resolve_preopen_host(str(outside), approved_roots=[str(root)])


def test_a_sibling_of_an_approved_root_is_not_treated_as_inside(tmp_path: Path) -> None:
    # `/workspace` must not cover the sibling `/workshop`: containment is by whole component.
    root = tmp_path / "work"
    root.mkdir()
    sibling = tmp_path / "workshop"
    sibling.mkdir()
    with pytest.raises(SandboxPathError, match="outside the approved workspace"):
        resolve_preopen_host(str(sibling), approved_roots=[str(root)])


def test_an_approved_root_reached_through_a_symlink_still_matches(tmp_path: Path) -> None:
    real_root = tmp_path / "real_root"
    (real_root / "in").mkdir(parents=True)
    linked_root = tmp_path / "linked_root"
    linked_root.symlink_to(real_root, target_is_directory=True)
    # The grant names the real directory; the operator's approved root is the symlink to it.
    resolved = resolve_preopen_host(str(real_root / "in"), approved_roots=[str(linked_root)])
    assert resolved == os.path.realpath(real_root / "in")


def test_a_second_approved_root_admits_a_path_under_it(tmp_path: Path) -> None:
    first = tmp_path / "first"
    first.mkdir()
    second = tmp_path / "second"
    inside = second / "in"
    inside.mkdir(parents=True)
    resolved = resolve_preopen_host(str(inside), approved_roots=[str(first), str(second)])
    assert resolved == os.path.realpath(inside)


def test_within_approved_root_treats_incomparable_paths_as_outside() -> None:
    # os.path.commonpath raises ValueError on a mix of absolute and relative paths; the
    # containment guard must swallow it and report "outside", never crash. This cannot arise
    # from resolve_preopen_host (its resolved paths are always absolute), so exercise the guard
    # directly with a relative path against an absolute root.
    from synapse_channel.core.sandbox_paths import _within_approved_root

    assert _within_approved_root("relative/not/absolute", ["/approved/root"]) is False


def test_check_preopen_host_threads_the_approved_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    check = check_preopen_host(str(outside), approved_roots=[str(root)])
    assert check.ok is False
    assert "outside the approved workspace" in check.reason


def test_harden_preopens_enforces_the_approved_root_across_the_set(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    good = root / "in"
    good.mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(SandboxPathError, match="outside the approved workspace"):
        harden_preopens(
            ((str(good), "/in", False), (str(outside), "/out", False)),
            approved_roots=[str(root)],
        )


def test_run_sandboxed_refuses_a_grant_outside_the_workspace_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    class _Unused:
        def __getattr__(self, name: str) -> object:  # pragma: no cover - must never be touched
            raise AssertionError(f"the runtime must not be used: {name}")

    receipt = run_sandboxed(
        _manifest_granting(str(outside)),
        b"wasm",
        b"in",
        runtime=_Unused(),
        approved_roots=[str(root)],
    )
    assert receipt["exit"] == EXIT_ERROR
    assert "sandbox path refused" in receipt["reason"]
    assert "outside the approved workspace" in receipt["reason"]
    assert receipt["preopened_paths"] == []


def _manifest_granting(host_path: str) -> CapabilityManifest:
    return CapabilityManifest(
        tool_id="tool",
        content_digest="sha256:" + "a" * 64,
        resources=ResourceGrant(memory_bytes=1 << 20, fuel=1_000, wall_clock_ms=1_000),
        filesystem=(FilesystemGrant(host_path=host_path, guest_path="/data", write=False),),
    )


def test_run_sandboxed_refuses_a_symlinked_grant_before_executing(tmp_path: Path) -> None:
    # The stand-in runtime would be used only by _execute; a refused grant never reaches it,
    # so the run fails closed with an error receipt and nothing is preopened or executed.
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    class _Unused:
        def __getattr__(self, name: str) -> object:  # pragma: no cover - must never be touched
            raise AssertionError(f"the runtime must not be used: {name}")

    receipt = run_sandboxed(_manifest_granting(str(link)), b"wasm", b"in", runtime=_Unused())
    assert receipt["exit"] == EXIT_ERROR
    assert "sandbox path refused" in receipt["reason"]
    assert "symlink" in receipt["reason"]
    assert receipt["preopened_paths"] == []
