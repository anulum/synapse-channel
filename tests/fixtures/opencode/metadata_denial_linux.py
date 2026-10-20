# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — native Linux adapter metadata refusal acceptance
"""Explicit Linux profile: refuse publication under a real child-only syscall policy."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from synapse_channel.opencode_adapter_files import read_text_snapshot, write_text_snapshot


def _deny_fchmod() -> None:
    """Install the libseccomp fchmod/EPERM rule in this disposable process only."""
    library = ctypes.CDLL("libseccomp.so.2")
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_void_p,
    ]
    library.seccomp_rule_add_array.restype = ctypes.c_int
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_load.restype = ctypes.c_int
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    library.seccomp_release.restype = None
    # Native API constants from libseccomp's public seccomp.h, not syscall numbers.
    context = library.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    assert context, "libseccomp could not allocate a filter"
    try:
        syscall = int(library.seccomp_syscall_resolve_name(b"fchmod"))
        assert syscall >= 0, "the native architecture must expose fchmod"
        result = library.seccomp_rule_add_array(context, 0x00050000 | errno.EPERM, syscall, 0, None)
        assert result == 0, f"libseccomp rejected the fchmod rule: {result}"
        result = library.seccomp_load(context)
        assert result == 0, f"the kernel refused the child-only filter: {result}"
    finally:
        library.seccomp_release(context)


def _exercise_refused_write(directory: Path, existed: bool) -> None:
    """Keep the target and descriptor set unchanged after native publication refusal."""
    path = directory / "config.json"
    if existed:
        path.write_text("original configuration")
        path.chmod(0o640)
    snapshot = read_text_snapshot(path)
    before = path.stat() if existed else None
    allocated: list[Path] = []

    def observe_allocation(event: str, arguments: tuple[object, ...]) -> None:
        """Record native temporary-file allocation without changing its execution."""
        if event == "tempfile.mkstemp":
            allocated.append(Path(str(arguments[0])))

    sys.addaudithook(observe_allocation)
    _deny_fchmod()
    descriptors = set(os.listdir("/proc/self/fd"))
    with pytest.raises(PermissionError) as failure:
        write_text_snapshot(path, "refused replacement", snapshot)
    assert failure.value.errno == errno.EPERM
    assert set(os.listdir("/proc/self/fd")) == descriptors
    assert len(allocated) == 1 and allocated[0].parent == directory
    assert not allocated[0].exists()
    assert list(directory.glob(".config.json.*")) == []
    if existed:
        assert path.stat() == before
        assert read_text_snapshot(path).text == "original configuration"
        assert path.stat().st_mode & 0o777 == 0o640
    else:
        assert not path.exists()
    print(json.dumps({"errno": failure.value.errno, "existed": existed, "temporary_removed": True}))


@pytest.mark.parametrize("existed", [False, True])
def test_kernel_metadata_refusal_preserves_the_target_and_closes_the_temporary_file(
    tmp_path: Path, existed: bool
) -> None:
    """Exercise native EPERM cleanup while an unconfined parent can still publish."""
    assert sys.platform == "linux", "collect this explicit profile on Linux only"
    control = tmp_path / "parent.json"
    write_text_snapshot(control, "before child", read_text_snapshot(control))
    confined = tmp_path / "confined"
    confined.mkdir(mode=0o700)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(confined), str(int(existed))],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "errno": errno.EPERM,
        "existed": existed,
        "temporary_removed": True,
    }
    write_text_snapshot(control, "after child", read_text_snapshot(control))
    assert read_text_snapshot(control).text == "after child"


if __name__ == "__main__":
    _exercise_refused_write(Path(sys.argv[1]), bool(int(sys.argv[2])))
