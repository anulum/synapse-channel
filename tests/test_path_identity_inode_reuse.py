# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — claim object identities survive inode reuse
"""A reused inode must not make an unrelated file look like a claimed one."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from synapse_channel.core.path_identity import ClaimScopeIdentity, claim_scopes_conflict
from synapse_channel.git.path_identity import resolve_claim_scope_identity

fcntl = pytest.importorskip("fcntl")

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="inode generations come from the Linux FS_IOC_GETVERSION ioctl",
)


def _reports_generations(directory: Path) -> bool:
    """Ask the filesystem directly, not the code under test, for an inode generation."""
    probe = directory / ".generation-probe"
    probe.write_text("", encoding="utf-8")
    descriptor = os.open(probe, os.O_RDONLY)
    try:
        fcntl.ioctl(descriptor, 0x80087601, bytes(8))
    except OSError:
        return False
    finally:
        os.close(descriptor)
        probe.unlink()
    return True


def _repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _identity(repo: Path, display: str) -> tuple[tuple[str, ...], ClaimScopeIdentity]:
    root, paths, identity = resolve_claim_scope_identity(repo, [display])
    return paths, identity


def _conflict(
    repo: Path,
    left: tuple[tuple[str, ...], ClaimScopeIdentity],
    right: tuple[tuple[str, ...], ClaimScopeIdentity],
) -> bool:
    worktree = str(repo.resolve())
    return claim_scopes_conflict(worktree, left[0], left[1], worktree, right[0], right[1])


def _reuse_inode(repo: Path, removed_inode: int) -> Path:
    """Create a batch of files, keep the one that received ``removed_inode``."""
    batch = [repo / f"candidate-{index}.txt" for index in range(512)]
    for path in batch:
        path.write_text("new file\n", encoding="utf-8")
    by_inode = {path.stat().st_ino: path for path in batch}
    newcomer = by_inode.get(removed_inode)
    assert newcomer is not None, "the filesystem did not reuse the inode within 512 new files"
    for path in batch:
        if path is not newcomer:
            path.unlink()
    return newcomer


def test_a_file_on_a_reused_inode_does_not_conflict_with_the_old_claim(tmp_path: Path) -> None:
    """The old claim's identity names the removed file, not its inode's next owner."""
    if not _reports_generations(tmp_path):
        pytest.skip("this filesystem reports no inode generation, so reuse cannot be told apart")
    repo = _repo(tmp_path / "repo")
    old = repo / "CONTRIBUTING.md"
    old.write_text("old file\n", encoding="utf-8")
    old_claim = _identity(repo, "CONTRIBUTING.md")
    removed_inode = old.stat().st_ino
    old.unlink()
    newcomer = _reuse_inode(repo, removed_inode)
    new_claim = _identity(repo, newcomer.name)
    assert old_claim[1].paths[0].object_id != new_claim[1].paths[0].object_id
    assert not _conflict(repo, old_claim, new_claim)


def test_a_hard_link_still_conflicts_with_its_original(tmp_path: Path) -> None:
    """Two names of one inode keep one identity, so claims on them conflict."""
    repo = _repo(tmp_path / "repo")
    original = repo / "shared.py"
    original.write_text("x = 1\n", encoding="utf-8")
    alias = repo / "alias.py"
    os.link(original, alias)
    first = _identity(repo, "shared.py")
    second = _identity(repo, "alias.py")
    assert first[1].paths[0].object_id == second[1].paths[0].object_id
    assert _conflict(repo, first, second)


def test_an_atomic_replace_of_the_claimed_path_still_conflicts(tmp_path: Path) -> None:
    """A new file at the claimed path has a new identity but still conflicts by path."""
    repo = _repo(tmp_path / "repo")
    claimed = repo / "CONTRIBUTING.md"
    claimed.write_text("first\n", encoding="utf-8")
    before = _identity(repo, "CONTRIBUTING.md")
    replacement = repo / ".CONTRIBUTING.md.tmp"
    replacement.write_text("second\n", encoding="utf-8")
    os.replace(replacement, claimed)
    after = _identity(repo, "CONTRIBUTING.md")
    assert before[1].paths[0].object_id != after[1].paths[0].object_id
    assert _conflict(repo, before, after)


def _legacy_key(path: Path) -> str:
    metadata = path.lstat()
    return f"{metadata.st_dev:x}:{metadata.st_ino:x}"


def test_special_and_unopenable_objects_keep_the_device_and_inode(tmp_path: Path) -> None:
    """A FIFO, a socket and an unreadable file are never given a generation."""
    repo = _repo(tmp_path / "repo")
    fifo = repo / "pipe"
    os.mkfifo(fifo, 0o600)
    unreadable = repo / "secret.txt"
    unreadable.write_text("x\n", encoding="utf-8")
    unreadable.chmod(0o000)
    node = repo / "listener.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(node))
        for path in (fifo, node, unreadable):
            assert _identity(repo, path.name)[1].paths[0].object_id == _legacy_key(path)
    finally:
        listener.close()
        unreadable.chmod(0o600)


def test_a_filesystem_without_generations_keeps_the_device_and_inode() -> None:
    """On tmpfs, which reports no inode generation, a file keeps device:inode."""
    if not Path("/dev/shm").is_dir():
        pytest.skip("no /dev/shm tmpfs on this host")
    base = Path(tempfile.mkdtemp(dir="/dev/shm"))
    try:
        repo = _repo(base / "repo")
        note = repo / "note.txt"
        note.write_text("x\n", encoding="utf-8")
        assert _identity(repo, "note.txt")[1].paths[0].object_id == _legacy_key(note)
    finally:
        shutil.rmtree(base)
