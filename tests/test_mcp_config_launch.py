# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound MCP executable, cwd, and environment launch tests

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import synapse_channel.core.mcp_config_launch as launch_module
from synapse_channel.core.mcp_config import McpConfigError, McpServerSpec
from synapse_channel.core.mcp_config_launch import (
    bind_mcp_server_launch,
    child_environment,
    validate_mcp_server_launch,
)
from synapse_channel.core.secret_files import open_nofollow_descriptor


def _executable(path: Path) -> tuple[Path, str]:
    shutil.copy2("/bin/true", path)
    path.chmod(0o700)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        (McpServerSpec(name="x", command="relative"), "absolute executable"),
        (McpServerSpec(name="x", command="/definitely/missing"), "cannot securely open"),
    ],
)
def test_launch_validation_rejects_unpinned_path_forms(spec: McpServerSpec, match: str) -> None:
    with pytest.raises(McpConfigError, match=match):
        validate_mcp_server_launch(spec)


def test_launch_validation_rejects_symlink_mode_hash_and_cwd_drift(tmp_path: Path) -> None:
    executable, digest = _executable(tmp_path / "mcp-server")
    link = tmp_path / "server-link"
    link.symlink_to(executable)
    with pytest.raises(McpConfigError, match="cannot securely open"):
        validate_mcp_server_launch(McpServerSpec(name="x", command=str(link)))

    executable.chmod(0o600)
    with pytest.raises(McpConfigError, match="not executable"):
        validate_mcp_server_launch(McpServerSpec(name="x", command=str(executable)))
    executable.chmod(0o700)

    with pytest.raises(McpConfigError, match="does not match"):
        validate_mcp_server_launch(
            McpServerSpec(name="x", command=str(executable), command_sha256="0" * 64)
        )
    evidence = validate_mcp_server_launch(
        McpServerSpec(name="x", command=str(executable), command_sha256=digest)
    )
    assert evidence.hash_pinned is True

    with pytest.raises(McpConfigError, match="cwd must be an absolute"):
        validate_mcp_server_launch(McpServerSpec(name="x", command=str(executable), cwd="relative"))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    cwd.chmod(0o700)
    cwd_link = tmp_path / "cwd-link"
    cwd_link.symlink_to(cwd, target_is_directory=True)
    with pytest.raises(McpConfigError, match="cannot securely open cwd"):
        validate_mcp_server_launch(
            McpServerSpec(name="x", command=str(executable), cwd=str(cwd_link))
        )
    assert validate_mcp_server_launch(
        McpServerSpec(name="x", command=str(executable), cwd=str(cwd))
    )
    cwd.chmod(0o770)
    with pytest.raises(McpConfigError, match="must not be group/world-writable"):
        validate_mcp_server_launch(McpServerSpec(name="x", command=str(executable), cwd=str(cwd)))
    cwd.chmod(0o700)
    with bind_mcp_server_launch(
        McpServerSpec(name="default-cwd", command=str(executable))
    ) as launch:
        assert os.path.samefile(launch.cwd, "/")


def test_launch_validation_rejects_symlinked_ancestors_and_raw_tilde(tmp_path: Path) -> None:
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    executable, _digest = _executable(real_directory / "mcp-server")
    ancestor_link = tmp_path / "via-link"
    ancestor_link.symlink_to(real_directory, target_is_directory=True)

    with pytest.raises(McpConfigError, match="cannot securely open executable"):
        validate_mcp_server_launch(
            McpServerSpec(name="x", command=str(ancestor_link / "mcp-server"))
        )
    with pytest.raises(McpConfigError, match="absolute executable"):
        validate_mcp_server_launch(McpServerSpec(name="x", command="~/mcp-server"))
    with pytest.raises(McpConfigError, match="cwd must be an absolute"):
        validate_mcp_server_launch(McpServerSpec(name="x", command=str(executable), cwd="~/cwd"))


def test_launch_validation_rejects_an_unbound_shebang_interpreter(tmp_path: Path) -> None:
    script = tmp_path / "mcp-server"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o700)

    with pytest.raises(McpConfigError, match="unbound shebang interpreter"):
        validate_mcp_server_launch(McpServerSpec(name="script", command=str(script)))


def test_launch_validation_rejects_directory_and_descriptor_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "not-an-executable"
    directory.mkdir()
    with pytest.raises(McpConfigError, match="not a regular executable"):
        validate_mcp_server_launch(McpServerSpec(name="x", command=str(directory)))

    executable, _digest = _executable(tmp_path / "mcp-server")

    def fail_read(_descriptor: int, _size: int) -> bytes:
        raise OSError("forced descriptor read failure")

    monkeypatch.setattr(os, "read", fail_read)
    with pytest.raises(McpConfigError, match="cannot inspect executable"):
        validate_mcp_server_launch(McpServerSpec(name="x", command=str(executable)))


def test_launch_snapshot_rejects_size_growth_and_identity_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, _digest = _executable(tmp_path / "mcp-server")
    monkeypatch.setattr(launch_module, "MCP_EXECUTABLE_SNAPSHOT_LIMIT", 1)
    with pytest.raises(McpConfigError, match="exceeds the 1-byte snapshot limit"):
        validate_mcp_server_launch(McpServerSpec(name="large", command=str(executable)))

    executable.write_bytes(b"")
    executable.chmod(0o700)
    reads = iter((b"xx", b""))
    monkeypatch.setattr(os, "read", lambda _descriptor, _size: next(reads))
    with pytest.raises(McpConfigError, match="grew beyond"):
        validate_mcp_server_launch(McpServerSpec(name="growing", command=str(executable)))

    monkeypatch.undo()
    executable, _digest = _executable(tmp_path / "stable-server")
    real_fstat = os.fstat
    calls = 0

    def drift_after_copy(descriptor: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        info = real_fstat(descriptor)
        if calls == 2:
            values = list(info)
            values[9] += 1
            return os.stat_result(values)
        return info

    monkeypatch.setattr(os, "fstat", drift_after_copy)
    with pytest.raises(McpConfigError, match="changed while snapshotting"):
        validate_mcp_server_launch(McpServerSpec(name="drift", command=str(executable)))


def test_launch_validation_fails_when_platform_cannot_prove_nofollow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "name", "nt")
    with pytest.raises(McpConfigError, match="unavailable on this platform"):
        validate_mcp_server_launch(McpServerSpec(name="x", command="C:/server.exe"))


def test_launch_validation_uses_effective_group_other_and_root_execute_bits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, _digest = _executable(tmp_path / "mcp-server")
    actual_uid = os.geteuid()
    actual_gid = executable.stat().st_gid
    monkeypatch.setattr(os, "geteuid", lambda: actual_uid + 10_000)
    monkeypatch.setattr(os, "getegid", lambda: actual_gid)
    monkeypatch.setattr(os, "getgroups", lambda: [])
    executable.chmod(0o610)
    assert validate_mcp_server_launch(McpServerSpec(name="group", command=str(executable)))

    monkeypatch.setattr(os, "getegid", lambda: actual_gid + 10_000)
    executable.chmod(0o601)
    assert validate_mcp_server_launch(McpServerSpec(name="other", command=str(executable)))

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    executable.chmod(0o500)
    assert validate_mcp_server_launch(McpServerSpec(name="root", command=str(executable)))


def test_launch_validation_rechecks_cwd_descriptor_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, _digest = _executable(tmp_path / "mcp-server")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    cwd.chmod(0o700)
    real_open = open_nofollow_descriptor

    def open_with_cwd_type_drift(path: str | Path, *, directory: bool = False) -> int:
        if directory:
            return real_open(executable)
        return real_open(path)

    monkeypatch.setattr(launch_module, "open_nofollow_descriptor", open_with_cwd_type_drift)
    with pytest.raises(McpConfigError, match="cwd.*is not a directory"):
        validate_mcp_server_launch(McpServerSpec(name="x", command=str(executable), cwd=str(cwd)))


def test_bound_launch_executes_snapshot_and_retains_exact_cwd(tmp_path: Path) -> None:
    executable = tmp_path / "mcp-server"
    shutil.copy2("/bin/echo", executable)
    executable.chmod(0o700)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    cwd.chmod(0o700)
    (cwd / "marker").write_text("trusted-cwd", encoding="utf-8")
    spec = McpServerSpec(
        name="bound",
        command=str(executable),
        cwd=str(cwd),
        command_sha256=digest,
    )

    with bind_mcp_server_launch(spec) as launch:
        replacement = tmp_path / "replacement"
        shutil.copy2("/bin/false", replacement)
        replacement.chmod(0o700)
        os.replace(replacement, executable)
        renamed_cwd = tmp_path / "renamed-cwd"
        cwd.rename(renamed_cwd)
        cwd.mkdir()
        (cwd / "marker").write_text("replacement-cwd", encoding="utf-8")

        assert subprocess.check_output([launch.command, "trusted"], text=True).strip() == "trusted"
        assert (Path(launch.cwd) / "marker").read_text(encoding="utf-8") == "trusted-cwd"


def test_procfd_binding_fails_closed_on_missing_or_mismatched_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(McpConfigError, match="cannot bind MCP launch descriptor"):
        launch_module._procfd_path(-1)

    path = tmp_path / "file"
    path.write_text("x", encoding="utf-8")
    descriptor = os.open(path, os.O_RDONLY)
    real_stat = os.stat

    def mismatched_stat(target: str | Path, *, follow_symlinks: bool = True) -> os.stat_result:
        # Signature-compatible with os.stat: on some platforms the procfd path stats
        # with follow_symlinks set, so the monkeypatched replacement must accept and
        # forward it rather than crash with an unexpected-keyword TypeError.
        info = real_stat(target, follow_symlinks=follow_symlinks)
        values = list(info)
        values[1] += 1
        return os.stat_result(values)

    try:
        monkeypatch.setattr(os, "stat", mismatched_stat)
        with pytest.raises(McpConfigError, match="binding mismatch"):
            launch_module._procfd_path(descriptor)
    finally:
        os.close(descriptor)


def test_child_environment_is_empty_unless_each_name_is_approved() -> None:
    spec = McpServerSpec(
        name="x",
        command="/bin/false",
        env={"EXPLICIT": "configured", "LANG": "override"},
        inherit_env=("LANG", "MISSING"),
    )

    environment = child_environment(
        spec,
        parent={"HOME": "/sensitive/home", "LANG": "parent", "SECRET": "must-not-leak"},
    )

    assert environment == {
        "HOME": "",
        "LOGNAME": "",
        "PATH": "",
        "SHELL": "",
        "TERM": "",
        "USER": "",
        "LANG": "override",
        "EXPLICIT": "configured",
    }
