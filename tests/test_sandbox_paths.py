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
    SandboxPathError,
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
    hardened = harden_preopens(
        ((str(read_dir), "/in", False), (str(write_dir), "/out", True))
    )
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
