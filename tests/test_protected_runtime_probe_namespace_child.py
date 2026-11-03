# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — declarative prerequisite namespace contracts
"""Check argv and enrollment text without executing or qualifying a namespace."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest


def _native_contract_profile() -> bool:
    """Refuse unsupported qualification backends before their platform-specific imports."""
    from tools.protected_runtime_probe.native_closure import require_native_runtime_abi

    observed = (
        sys.platform,
        platform.machine(),
        sys.version_info[:2],
        sys.maxsize.bit_length() + 1,
    )
    if observed != ("linux", "x86_64", (3, 12), 64):
        with pytest.raises(OSError, match="native Linux"):
            require_native_runtime_abi(*observed)
        return False
    return True


@pytest.mark.parametrize("profile", [False, True, 0, 1, None, "true"])
def test_declarative_namespace_profile_has_exact_argv(profile: object) -> None:
    """Check the declared launch contract, without treating its handles as kernel facts."""
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.namespace_child import _mount_arguments

    config = {
        "captured_runtime": profile,
        "view": "/reviewed/code",
        "endpoints": "/owned/endpoints",
        "data_fd": 37,
    }
    if type(profile) is not bool:
        with pytest.raises(ValueError, match="invalid namespace runtime profile"):
            _mount_arguments(config)
        return
    expected = [
        "/usr/bin/bwrap",
        "--die-with-parent",
        "--new-session",
        "--as-pid-1",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-ipc",
        "--unshare-uts",
        "--hostname",
        "synapse-prerequisite",
        "--chdir",
        "/",
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/sbin:/usr/bin:/sbin:/bin",
        "--setenv",
        "LC_ALL",
        "C",
    ]
    if not profile:
        expected += [
            "--ro-bind",
            "/usr",
            "/usr",
            "--symlink",
            "usr/bin",
            "/bin",
            "--symlink",
            "usr/lib",
            "/lib",
            "--symlink",
            "usr/lib64",
            "/lib64",
            "--ro-bind",
            "/etc/passwd",
            "/etc/passwd",
            "--ro-bind",
            "/etc/group",
            "/etc/group",
            "--ro-bind",
            "/etc/nsswitch.conf",
            "/etc/nsswitch.conf",
        ]
    expected += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--dir",
        "/sys/fs",
        "--ro-bind",
        "/sys/fs/cgroup",
        "/sys/fs/cgroup",
        "--ro-bind",
        "/reviewed/code",
        "/code",
        "--ro-bind",
        "/owned/endpoints",
        "/endpoints",
        "--bind-fd",
        "37",
        "/data",
        "--cap-drop",
        "ALL",
    ]
    if profile:
        expected += [
            "--ro-bind",
            "/sys/devices/system/cpu/online",
            "/sys/devices/system/cpu/online",
        ]
    assert _mount_arguments(config) == expected


@pytest.mark.parametrize(
    "case", ["valid", "boundary", "oversized", "array", "extra", "missing", "invalid-json"]
)
def test_declarative_kernel_enrollment_text_contract(case: str) -> None:
    """Decode declared fields and bounds; parsing is not independent kernel enrollment."""
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.namespace_child import _enrolled_policy

    from synapse_channel.core.protected_write_kernel_boundary import (
        ProtectedRootKernelIdentity,
        ProtectedWriterKernelPolicy,
    )

    config = {
        "writer_uid": 986,
        "writer_gid": 977,
        "shared_uid": 1000,
        "host_namespace": [2, 99],
    }
    record: dict[str, object] = {
        "mount_namespace": [2, 101],
        "mountinfo_sha256": "1" * 64,
        "root": [3, 42, 7],
        "subtree_identity": [5, 22],
    }
    if case == "extra":
        record["unexpected"] = True
    elif case == "missing":
        del record["root"]
    raw = json.dumps(record, separators=(",", ":")).encode("ascii")
    if case == "boundary":
        raw += b" " * (8192 - len(raw))
        assert len(raw) == 8192
    elif case == "oversized":
        raw += b" " * (8193 - len(raw))
    elif case == "array":
        raw = b"[]"
    elif case == "invalid-json":
        raw = b"{"
    if case == "oversized":
        with pytest.raises(ValueError, match="exceeds byte bound"):
            _enrolled_policy(raw, config)
    elif case in {"array", "extra", "missing"}:
        with pytest.raises(ValueError, match="enrollment shape differs"):
            _enrolled_policy(raw, config)
    elif case == "invalid-json":
        with pytest.raises(json.JSONDecodeError):
            _enrolled_policy(raw, config)
    else:
        assert _enrolled_policy(raw, config) == ProtectedWriterKernelPolicy(
            writer_uid=986,
            writer_gid=977,
            shared_uid=1000,
            host_mount_namespace=(2, 99),
            writer_mount_namespace=(2, 101),
            mountinfo_sha256="1" * 64,
            roots=(ProtectedRootKernelIdentity("/data", 3, 42, 7),),
            writable_mount_targets=frozenset({"/data"}),
            writer_subtree_identity=(5, 22),
        )


@pytest.mark.parametrize(
    "case",
    [
        "missing-selector",
        "missing-enrollment",
        "incomplete",
        "extra",
        "host-tools",
        "stdio",
        "boolean-fd",
        "digest",
    ],
)
def test_actual_namespace_entry_refuses_profile_disagreement_before_namespace_construction(
    case: str,
) -> None:
    """Run the real entry in a single-UID private namespace, with no host account effects."""
    if not _native_contract_profile():
        return
    import websockets

    record: dict[str, object] = {"manifest_sha256": "a" * 64, "output_fd": 9}
    config: dict[str, object] = {
        "captured_runtime": True,
        "writer_uid": 0,
        "writer_gid": 0,
        "instrumentation": record,
    }
    arguments = ["--instrumentation"]
    if case == "missing-selector":
        arguments = []
    elif case == "missing-enrollment":
        del config["instrumentation"]
    elif case == "incomplete":
        del record["output_fd"]
    elif case == "extra":
        record["unexpected"] = True
    elif case == "host-tools":
        config["captured_runtime"] = False
    elif case == "stdio":
        record["output_fd"] = 0
    elif case == "boolean-fd":
        record["output_fd"] = True
    elif case == "digest":
        record["manifest_sha256"] = "invalid"
    source = Path(__file__).resolve().parents[1]
    entry = """import sys,runpy
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/src',sys.argv[2]]
sys.argv=['namespace_child',*sys.argv[3:]]
runpy.run_module('tools.protected_runtime_probe.namespace_child',run_name='__main__')
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
        "--tmpfs",
        "/",
    ]
    for directory in ("/usr", "/media", "/home", "/lib", "/lib64"):
        command += ["--ro-bind", directory, directory]
    command += [
        "--proc",
        "/proc",
        "--cap-drop",
        "ALL",
        "/usr/bin/python3.12",
        "-I",
        "-B",
        "-S",
        "-c",
        entry,
        str(source),
        str(Path(websockets.__file__).parents[1]),
        *arguments,
    ]
    result = subprocess.run(
        command,
        input=json.dumps(config) + "\n",
        capture_output=True,
        text=True,
        timeout=20,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        check=False,
    )
    assert result.returncode == 1, result.stderr
    expected = (
        "selector differs"
        if case in {"missing-selector", "missing-enrollment"}
        else "manifest pin"
        if case == "digest"
        else "invalid writer instrumentation enrollment"
    )
    assert expected in result.stderr
    assert not result.stdout
