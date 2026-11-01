# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real kernel cgroup control custody tests
"""Read actual kernel control ancestry and refuse user delegation."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from fixtures.protected_write_subtree_linux import delegated_subtree as delegated_subtree
from synapse_channel.core.protected_write_cgroup_controls import (
    capture_protected_cgroup_controls,
    verify_protected_cgroup_controls,
)


def test_actual_kernel_root_controls_are_root_owned() -> None:
    """Validate real root control metadata without implying a writer admission."""
    descriptor = os.open("/sys/fs/cgroup", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        levels = capture_protected_cgroup_controls(descriptor)
        assert len(levels) == 1 and levels[0].kind == "root"
        assert {name for name, _ in levels[0].controls} == {
            "cgroup.procs",
            "cgroup.threads",
            "cgroup.subtree_control",
        }
        verify_protected_cgroup_controls(levels)
        identity = os.fstat(descriptor)
        assert levels[0].directory.inode == identity.st_ino
    finally:
        os.close(descriptor)


def test_actual_delegation_ancestry_is_preserved_and_refused(delegated_subtree: Path) -> None:
    """User-controlled membership/delegation cannot become root custody proof."""
    descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        levels = capture_protected_cgroup_controls(descriptor)
        assert len(levels) > 1
        assert levels[0].directory.uid == os.geteuid()
        assert levels[-1].directory.inode == Path("/sys/fs/cgroup").stat().st_ino
        assert "cgroup.freeze" in dict(levels[0].controls)
        with pytest.raises(ValueError, match="agent-accessible"):
            verify_protected_cgroup_controls(levels)
        with pytest.raises(ValueError, match="ancestry exceeds budget"):
            capture_protected_cgroup_controls(descriptor, max_depth=1)
    finally:
        os.close(descriptor)


def test_regular_filesystem_ancestry_is_not_kernel_control_evidence(tmp_path: Path) -> None:
    """Permission-shaped files on an ordinary filesystem cannot supply custody."""
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError, match="outside the kernel filesystem"):
            capture_protected_cgroup_controls(descriptor)
        for budget in (True, 0, 257):
            with pytest.raises(ValueError, match="invalid cgroup ancestry budget"):
                capture_protected_cgroup_controls(descriptor, max_depth=budget)
    finally:
        os.close(descriptor)


def test_control_capture_refuses_a_file_and_closed_descriptor(tmp_path: Path) -> None:
    """Refuse actual regular-file and closed descriptors before custody capture."""
    path = tmp_path / "control"
    path.write_text("0")
    descriptor = os.open(path, os.O_RDONLY)
    try:
        with pytest.raises(ValueError, match="outside the kernel filesystem"):
            capture_protected_cgroup_controls(descriptor)
    finally:
        os.close(descriptor)
    with pytest.raises(OSError):
        capture_protected_cgroup_controls(descriptor)


def test_absent_ancestry_cannot_prove_control_custody() -> None:
    """Reject an empty control ancestry instead of inferring root custody."""
    with pytest.raises(ValueError, match="ancestry is unavailable"):
        verify_protected_cgroup_controls(())


def test_real_directory_permission_race_refuses_control_capture(delegated_subtree: Path) -> None:
    """Actual metadata changes cannot become a stable custody observation."""
    descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    mode = delegated_subtree.stat().st_mode & 0o777
    stop, started = threading.Event(), threading.Event()

    def change_permissions() -> None:
        """Alternate actual directory modes until the observer stops the race."""
        started.set()
        while not stop.is_set():
            os.chmod(delegated_subtree, 0o700)
            os.chmod(delegated_subtree, 0o755)

    changer = threading.Thread(target=change_permissions)
    changer.start()
    try:
        assert started.wait(timeout=5)
        refused = False
        deadline = time.monotonic() + 5
        while not refused:
            assert time.monotonic() < deadline, "permission race was not observed before deadline"
            try:
                capture_protected_cgroup_controls(descriptor)
            except ValueError as error:
                assert str(error) == "cgroup control directory changed during capture"
                refused = True
        assert refused
    finally:
        stop.set()
        changer.join(timeout=5)
        assert not changer.is_alive()
        os.chmod(delegated_subtree, mode)
        os.close(descriptor)


def test_real_threaded_subtree_is_refused(delegated_subtree: Path) -> None:
    """Kernel thread membership must not be treated as whole-process custody."""
    descriptor = os.open(delegated_subtree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        (delegated_subtree / "cgroup.type").write_text("threaded")
        levels = capture_protected_cgroup_controls(descriptor)
        assert levels[0].kind == "threaded"
        with pytest.raises(ValueError, match="not an ordinary domain"):
            verify_protected_cgroup_controls(levels)
    finally:
        os.close(descriptor)


def test_private_namespace_refuses_cross_filesystem_control(tmp_path: Path) -> None:
    """A real bind over a kernel control must not be accepted as that kernel filesystem."""
    source = Path(__file__).resolve().parents[2]
    replacement = tmp_path / "not-a-kernel-control"
    replacement.write_text("ordinary Samsung bytes")
    script = """import os,sys
from pathlib import Path
sys.path[:0]=[sys.argv[1]+'/src',sys.argv[2]]
recorder=None
if sys.argv[3]:
 import coverage
 recorder=coverage.Coverage(data_file=str(Path(sys.argv[3])/'.coverage'),data_suffix=True,branch=True,source=['synapse_channel.core.protected_write_cgroup_controls'],config_file=False)
 recorder.start()
from synapse_channel.core.protected_write_cgroup_controls import capture_protected_cgroup_controls
descriptor=os.open('/sys/fs/cgroup',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
 try: capture_protected_cgroup_controls(descriptor)
 except ValueError as error: assert str(error)=='invalid kernel cgroup control file'
 else: raise AssertionError('a foreign control mount was accepted')
finally:
 os.close(descriptor)
 if recorder is not None:
  recorder.stop();recorder.save()
"""
    command = [
        "/usr/bin/bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-user",
        "--uid",
        "0",
        "--gid",
        "0",
        "--unshare-net",
        "--ro-bind",
        "/",
        "/",
        "--ro-bind",
        str(replacement),
        "/sys/fs/cgroup/cgroup.procs",
    ]
    coverage_dir = os.environ.get("SYNAPSE_TEST_COVERAGE_DIR", "")
    if coverage_dir:
        destination = Path(coverage_dir)
        assert destination.is_absolute() and destination.is_dir()
        command.extend(("--bind", coverage_dir, coverage_dir))
    command.extend(
        (
            sys.executable,
            "-I",
            "-B",
            "-c",
            script,
            str(source),
            str(Path(pytest.__file__).parents[1]),
            coverage_dir,
        )
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    assert replacement.read_text() == "ordinary Samsung bytes"
