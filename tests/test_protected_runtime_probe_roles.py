# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — actual parent account and account-free namespace regression
"""Use native user namespaces and local account files without simulated credential calls."""

from __future__ import annotations

import ast
import ctypes
import errno
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from tools.protected_runtime_probe.prepare import prepare_packet
from tools.protected_runtime_probe.roles import drop_enrolled_identity

from fixtures.capable_bwrap import capable_bwrap
from fixtures.protected_runtime_role_boundary import measured_entry_command


def _native_profile_or_refusal(tmp_path: Path) -> bool:
    """Exercise genuine unsupported-profile refusal outside the native captured ABI."""
    if (
        sys.platform == "linux"
        and platform.machine() == "x86_64"
        and sys.version_info[:2] == (3, 12)
    ):
        return True
    source = Path(__file__).resolve().parents[1]
    with pytest.raises(OSError, match="native Linux"):
        prepare_packet(source, tmp_path / "packet", tmp_path / "runs")
    return False


def _boundary(
    tmp_path: Path,
    *,
    method: str = "numeric",
    with_nss: bool = False,
    identity: dict[str, object] | None = None,
    parent_change: str | None = None,
    measurement_directory: Path | None = None,
    measure_operations: bool = True,
) -> dict[str, Any]:
    """Run real NSS validation and a separate mount-root credential API call."""
    assert os.geteuid() != 0, "native unprivileged regression requires the ordinary test runner"
    source = Path(__file__).resolve().parents[1]
    etc = tmp_path / "accounts"
    etc.mkdir()
    names = ("synapse-authority", "synapse-operator", "synapse-writer", "synapse-client")
    passwd = []
    groups = []
    shadow = []
    for index, name in enumerate(names):
        if name == "synapse-writer" and parent_change == "account-missing":
            continue
        uid = gid = 900 + index
        home, shell, members = "/nonexistent", "/usr/sbin/nologin", ""
        if name == "synapse-writer":
            if parent_change == "uid":
                uid = 899
            elif parent_change == "gid":
                gid = 899
            elif parent_change == "home":
                home = "/home/unqualified"
            elif parent_change == "shell":
                shell = "/bin/sh"
            elif parent_change == "members":
                members = "unqualified"
            elif parent_change == "uid-range":
                uid = 1001
            elif parent_change == "gid-range":
                gid = 1001
            elif parent_change == "uid-lower-bound":
                uid = 100
            elif parent_change == "gid-lower-bound":
                gid = 100
        placeholder = "x"
        if name == "synapse-writer" and parent_change == "passwd-empty":
            placeholder = ""
        elif name == "synapse-writer" and parent_change == "passwd-inline":
            placeholder = "disabled-test-record"
        passwd.append(f"{name}:{placeholder}:{uid}:{gid}:fixture:{home}:{shell}\n")
        groups.append(f"{name}:x:{gid}:{members}\n")
        password = "" if name == "synapse-writer" and parent_change == "password-empty" else "!"
        if name == "synapse-writer" and parent_change == "password-unlocked":
            password = "disabled-test-record"
        if name != "synapse-writer" or parent_change != "password-missing":
            shadow.append(f"{name}:{password}:0:0:99999:7:::\n")
    if parent_change == "supplementary":
        groups.append("unqualified:x:1500:synapse-writer\n")
    (etc / "passwd").write_text("".join(passwd))
    (etc / "group").write_text("".join(groups))
    (etc / "shadow").write_text("".join(shadow))
    (etc / "shadow").chmod(0o600)
    configuration = (
        "# Private account-tool configuration\n\nMAIL_DIR /var/mail\nUID_MIN 1000\nGID_MIN 1000\n"
    )
    if parent_change == "configured-range":
        configuration += "SYS_UID_MIN 800\nSYS_UID_MAX 950\nSYS_GID_MIN 850\nSYS_GID_MAX 960\n"
    if parent_change == "config-duplicate":
        configuration += "UID_MIN 1000\n"
    elif parent_change == "config-missing-value":
        configuration += "SYS_UID_MIN\n"
    elif parent_change == "config-nonnumeric":
        configuration += "SYS_GID_MIN unknown\n"
    elif parent_change == "config-zero-min":
        configuration += "SYS_UID_MIN 0\n"
    elif parent_change == "config-inverted-range":
        configuration += "SYS_GID_MIN 950\nSYS_GID_MAX 900\n"
    elif parent_change == "config-regular-overlap":
        configuration += "SYS_UID_MAX 1000\n"
    (etc / "login.defs").write_text(configuration)
    (etc / "nsswitch.conf").write_text(
        "passwd: unavailable_backend\ngroup: unavailable_backend\nshadow: unavailable_backend\n"
        if parent_change == "nss-unavailable"
        else "passwd: files\ngroup: files\nshadow: files\n"
    )
    prefix = Path(sys.base_prefix).resolve()
    executable = Path(sys.executable).resolve().relative_to(prefix).as_posix()
    fixture = source / "tests/fixtures/protected_runtime_role_boundary.py"
    namespace_tool = capable_bwrap()
    command = [
        namespace_tool,
        "--unshare-user",
        "--uid",
        "0",
        "--gid",
        "0",
        "--unshare-net",
    ]
    if parent_change == "home-exists":
        command.extend(("--tmpfs", "/"))
        for directory in ("/usr", "/etc", "/media", "/home", "/lib", "/lib64", str(prefix)):
            command.extend(("--ro-bind", directory, directory))
    else:
        command.extend(("--ro-bind", "/", "/"))
    command.extend(("--proc", "/proc"))
    for name in ("passwd", "group", "shadow", "login.defs", "nsswitch.conf"):
        command.extend(("--ro-bind", str(etc / name), "/etc/" + name))
    if parent_change == "home-exists":
        command.extend(("--dir", "/nonexistent"))
    coverage_dir = (
        str(measurement_directory)
        if measurement_directory is not None
        else os.environ.get("SYNAPSE_TEST_COVERAGE_DIR")
    )
    if coverage_dir:
        import coverage

        output = Path(coverage_dir)
        assert output.is_absolute() and output.is_dir() and not output.is_symlink()
        command.extend(("--bind", str(output), str(output)))
    command.extend(("--cap-drop", "ALL"))
    for capability in ("CAP_SYS_ADMIN", "CAP_SETPCAP", "CAP_SETUID", "CAP_SETGID"):
        command.extend(("--cap-add", capability))
    interpreter = [sys.executable, "-I", "-B"]
    entry_arguments = [
        "parent",
        "--probe",
        str(source / "tools"),
        "--account-files",
        str(etc),
        "--prefix",
        str(prefix),
        "--executable",
        executable,
        "--loader",
        str(Path("/lib64/ld-linux-x86-64.so.2").resolve()),
        "--bwrap",
        namespace_tool,
        "--method",
        method,
    ]
    if with_nss:
        entry_arguments.append("--with-nss")
    if identity is not None:
        entry_arguments.extend(("--identity", json.dumps(identity)))
    if coverage_dir:
        if measure_operations:
            entry_arguments.extend(
                (
                    "--coverage-dir",
                    str(output),
                    "--coverage-package",
                    str(Path(coverage.__file__).parent),
                )
            )
        command.extend(
            measured_entry_command(
                interpreter, fixture, entry_arguments, output, Path(coverage.__file__).parent
            )
        )
    else:
        command.extend((*interpreter, str(fixture), *entry_arguments))
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": "/nonexistent"},
    )
    assert result.returncode == 0, result.stderr
    value: dict[str, Any] = json.loads(result.stdout)
    return value


@pytest.mark.parametrize("with_nss", [False, True])
@pytest.mark.parametrize("method", ["numeric", "named"])
def test_real_parent_and_account_free_child_credential_boundary(
    tmp_path: Path, method: str, with_nss: bool
) -> None:
    """Retained numeric credentials reach kernel refusal even without NSS files.

    The single-UID user namespace cannot accept the distinct role. Its actual
    syscall refusal is expected, not a successful host credential transition.
    The named host API remains an explicit missing-NSS negative control.
    """
    if not _native_profile_or_refusal(tmp_path):
        return
    result = _boundary(tmp_path, method=method, with_nss=with_nss)
    assert result["parent_role"] == {"name": "synapse-writer", "uid": 902, "gid": 902}
    child = result["child"]
    assert child["host_usr_absent"] and child["passwd_present"] is with_nss
    assert child["uids_before"] == child["uids_after"] == [0, 0, 0]
    if method == "named" and not with_nss:
        assert child["error"] == "ValueError" and child["errno"] is None
        assert "actual system account" in child["message"]
    else:
        assert child["error"] in {"PermissionError", "OSError"}
        assert child["errno"] in {errno.EPERM, errno.EINVAL}
        assert "prerequisite" not in child["message"]


def test_measured_boundary_binds_both_real_entries_and_credential_api(tmp_path: Path) -> None:
    """Retain source-bound observations from both actual mount-root CLI entries."""
    if not _native_profile_or_refusal(tmp_path):
        return
    from coverage import CoverageData

    source = Path(__file__).resolve().parents[1]
    fixture = source / "tests/fixtures/protected_runtime_role_boundary.py"
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    result = _boundary(tmp_path, measurement_directory=recordings)
    assert result["phase"] == "child_refused"
    assert result["child"]["errno"] in {errno.EPERM, errno.EINVAL}
    observed_entries: set[str] = set()
    observed_data: set[Path] = set()
    credential_api_recorded = False
    for binding in recordings.glob("source-binding-*.json"):
        record = json.loads(binding.read_bytes())
        filename = record["data_filename"]
        assert Path(filename).name == filename
        path = recordings / filename
        observed_data.add(path)
        data = CoverageData(basename=str(path))
        data.read()
        assert data.has_arcs()
        sources = {entry["path"]: entry for entry in record["sources"]}
        assert set(data.measured_files()) <= sources.keys()
        for observed in data.measured_files():
            if observed == "/boundary.py":
                actual = fixture
            elif observed.startswith("/probe/tools/protected_runtime_probe/"):
                actual = source / observed.removeprefix("/probe/")
            else:
                actual = Path(observed)
            assert actual.resolve(strict=True).is_relative_to(source)
            assert hashlib.sha256(actual.read_bytes()).hexdigest() == sources[observed]["sha256"]
            if actual == fixture:
                assert data.arcs(observed)
                observed_entries.add(observed)
            if actual == source / "tools/protected_runtime_probe/roles.py":
                credential_api_recorded |= bool(data.arcs(observed))
    assert observed_entries == {str(fixture), "/boundary.py"}
    assert credential_api_recorded
    assert observed_data == set(recordings.glob(".coverage.*"))


def test_entry_measurement_preserves_unmeasured_namespace_operation(tmp_path: Path) -> None:
    """The optional operation collector remains absent during a real entry-only run."""
    if not _native_profile_or_refusal(tmp_path):
        return
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    result = _boundary(tmp_path, measurement_directory=recordings, measure_operations=False)
    assert result["phase"] == "child_refused"
    assert result["child"]["errno"] in {errno.EPERM, errno.EINVAL}
    bindings = list(recordings.glob("source-binding-*.json"))
    assert len(bindings) == 1
    binding = json.loads(bindings[0].read_bytes())
    assert [entry["module"] for entry in binding["sources"]] == ["__main__"]
    assert (recordings / binding["data_filename"]).is_file()


def test_boundary_retains_real_namespace_child_failure(tmp_path: Path) -> None:
    """A malformed real child command retains its original nonzero outcome."""
    if not _native_profile_or_refusal(tmp_path):
        return
    with pytest.raises(AssertionError, match="native credential fixture failed") as failure:
        _boundary(tmp_path, identity={"uid": 902})
    assert "KeyError: 'gid'" in str(failure.value)


def test_failed_collector_start_leaves_no_observation_binding(tmp_path: Path) -> None:
    """An actual unsupported collector core fails before the CLI emits measurement evidence."""
    import coverage

    source = Path(__file__).resolve().parents[1]
    recordings = tmp_path / "failed-start-recordings"
    recordings.mkdir()
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("COVERAGE_")
    }
    environment.update(TMPDIR=str(tmp_path), COVERAGE_CORE="unsupported-role-boundary-test-core")
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(source / "tests/fixtures/protected_runtime_role_boundary.py"),
            "child",
            "--identity",
            '{"uid":902,"gid":902}',
            "--coverage-dir",
            str(recordings),
            "--coverage-package",
            str(Path(coverage.__file__).parent),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 1 and not result.stdout
    assert "ConfigError: Unknown core value" in result.stderr.splitlines()[-1]
    assert "in trace" in result.stderr
    assert not list(recordings.iterdir())


@pytest.mark.parametrize("field", ["uid", "gid"])
@pytest.mark.parametrize("value", [0, -1, 1000, True, 2**32 - 1, 2**32, 1.5, "1"])
def test_numeric_child_rejects_shared_sentinel_and_invalid_ids_before_drop(
    tmp_path: Path, field: Literal["uid", "gid"], value: object
) -> None:
    """A real root-in-user-namespace child rejects invalid numbers without NSS."""
    if not _native_profile_or_refusal(tmp_path):
        return
    identity: dict[str, object] = {"uid": 902, "gid": 902}
    identity[field] = value
    child = _boundary(tmp_path, identity=identity)["child"]
    assert child["error"] == "ValueError" and "numeric identity" in child["message"]
    assert child["uids_before"] == child["uids_after"] == [0, 0, 0]
    assert not child["passwd_present"]


@pytest.mark.parametrize("change", ["uid", "gid", "home", "shell", "members"])
def test_trusted_parent_refuses_changed_real_account_metadata(tmp_path: Path, change: str) -> None:
    """Actual private passwd/group records cannot replace the exact enrolled role."""
    if not _native_profile_or_refusal(tmp_path):
        return
    result = _boundary(tmp_path, parent_change=change)
    assert result["phase"] == "parent_refused" and result["error"] == "ValueError"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("supplementary", "supplementary group"),
        ("passwd-empty", "reviewed disabled account"),
        ("passwd-inline", "reviewed disabled account"),
        ("password-empty", "password is not locked"),
        ("password-unlocked", "password is not locked"),
        ("password-missing", "no password lock record"),
        ("uid-range", "system identity range"),
        ("gid-range", "system identity range"),
        ("uid-lower-bound", "system identity range"),
        ("gid-lower-bound", "system identity range"),
        ("home-exists", "reviewed disabled account"),
        ("config-duplicate", "range configuration"),
        ("config-missing-value", "range configuration"),
        ("config-nonnumeric", "range configuration"),
        ("config-zero-min", "range configuration"),
        ("config-inverted-range", "range configuration"),
        ("config-regular-overlap", "range configuration"),
    ],
)
def test_trusted_parent_requires_locked_private_system_account(
    tmp_path: Path, change: str, message: str
) -> None:
    """Real NSS records must meet each disabled system role enrollment condition."""
    if not _native_profile_or_refusal(tmp_path):
        return
    result = _boundary(tmp_path, parent_change=change)
    assert result["phase"] == "parent_refused" and result["error"] == "ValueError"
    assert message in result["message"]


def test_trusted_parent_uses_configured_system_account_ranges(tmp_path: Path) -> None:
    """The real account backend honours explicit ranges rather than regular user IDs."""
    if not _native_profile_or_refusal(tmp_path):
        return
    result = _boundary(tmp_path, parent_change="configured-range")
    assert result["phase"] == "child_refused"
    assert result["parent_role"] == {"name": "synapse-writer", "uid": 902, "gid": 902}


def test_unprivileged_numeric_transition_refuses_before_effects() -> None:
    """The public account-free transition does not confer host root authority."""
    if sys.platform != "linux":
        with pytest.raises(OSError, match="native Linux"):
            drop_enrolled_identity(65532, 65532)
        return
    before = (os.getresuid(), os.getresgid(), os.getgroups())
    assert os.geteuid() != 0
    with pytest.raises(PermissionError, match="root child"):
        drop_enrolled_identity(65532, 65532)
    assert (os.getresuid(), os.getresgid(), os.getgroups()) == before


@pytest.mark.parametrize("active", [False, True])
def test_real_process_scan_for_empty_and_live_identity_sets(tmp_path: Path, active: bool) -> None:
    """Observe actual proc credentials without enrolling the observation-only role."""
    if not _native_profile_or_refusal(tmp_path):
        return
    from tools.protected_runtime_probe.roles import Role, _refuse_active_roles

    before = (os.getresuid(), os.getresgid(), os.getgroups())
    assert os.geteuid() != 0
    roles = (
        {"observation-only": Role("observation-only", os.getuid(), os.getgid())} if active else {}
    )
    if active:
        with pytest.raises(ValueError, match="already has a live process"):
            _refuse_active_roles(roles)
    else:
        _refuse_active_roles(roles)
    assert (os.getresuid(), os.getresgid(), os.getgroups()) == before


@pytest.mark.parametrize("field", ["uid", "gid"])
@pytest.mark.parametrize("value", [0, -1, 1000, True, 2**32 - 1, 2**32])
def test_public_role_validation_rejects_invalid_numeric_target(
    tmp_path: Path, field: str, value: int
) -> None:
    if not _native_profile_or_refusal(tmp_path):
        return
    from tools.protected_runtime_probe.roles import Role, validate_role_identity

    uid, gid = (value, 899) if field == "uid" else (899, value)
    before = (os.getresuid(), os.getresgid(), os.getgroups())
    with pytest.raises(ValueError, match="numeric identity"):
        validate_role_identity(Role("synapse-writer", uid, gid))
    assert (os.getresuid(), os.getresgid(), os.getgroups()) == before


def test_public_role_validation_rejects_unregistered_name(tmp_path: Path) -> None:
    if not _native_profile_or_refusal(tmp_path):
        return
    from tools.protected_runtime_probe.roles import Role, validate_role_identity

    with pytest.raises(ValueError, match="target role"):
        validate_role_identity(Role("unregistered", 899, 899))


def test_public_role_validation_refuses_missing_actual_account(tmp_path: Path) -> None:
    if not _native_profile_or_refusal(tmp_path):
        return
    result = _boundary(tmp_path, parent_change="account-missing")
    assert result["phase"] == "parent_refused" and result["error"] == "ValueError"
    assert "actual system account" in result["message"]


def test_privilege_call_reports_real_kernel_invalid_option(tmp_path: Path) -> None:
    if not _native_profile_or_refusal(tmp_path):
        return
    from tools.protected_runtime_probe.roles import _prctl

    before = (os.getresuid(), os.getresgid(), os.getgroups())
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_ulong] * 5
    libc.prctl.restype = ctypes.c_int
    with pytest.raises(OSError) as error:
        _prctl(libc, -1, 0)
    assert error.value.errno == errno.EINVAL
    assert (os.getresuid(), os.getresgid(), os.getgroups()) == before


@pytest.mark.parametrize("observed", [None, "win32", "darwin"])
@pytest.mark.parametrize(
    "operation", ["prerequisite account validation", "prerequisite credential transition"]
)
def test_role_platform_observation_preserves_exact_refusal(
    observed: str | None, operation: str
) -> None:
    """Check host or declared platform values without claiming foreign OS execution."""
    from tools.protected_runtime_probe import require_native_linux

    platform_value = sys.platform if observed is None else observed
    if platform_value == "linux":
        require_native_linux(platform_value, operation=operation)
    else:
        with pytest.raises(OSError) as error:
            require_native_linux(platform_value, operation=operation)
        assert str(error.value) == operation + " requires native Linux"


def test_public_role_validation_uses_only_local_files_without_nss_backend(tmp_path: Path) -> None:
    """The real account API works when NSS configuration names no available backend."""
    if not _native_profile_or_refusal(tmp_path):
        return
    result = _boundary(tmp_path, parent_change="nss-unavailable")
    assert result["phase"] == "child_refused"
    assert result["parent_role"] == {"name": "synapse-writer", "uid": 902, "gid": 902}
    assert result["account_backend_modules_loaded"] == []


@pytest.mark.parametrize("case", ["inherited-groups", "uid-mismatch", "gid-mismatch"])
def test_real_parent_refuses_inherited_or_mismatched_writer_credentials(
    tmp_path: Path, case: str
) -> None:
    """Reject an owned namespace child whose host credentials cannot meet enrollment.

    An ordinary user namespace retains the host supplementary groups. Refusal
    must leave the actual marker absent. Successful dedicated host principal
    observation remains an explicit operator qualification requirement.
    """
    if not _native_profile_or_refusal(tmp_path):
        return
    from tools.protected_runtime_probe.roles import observe_probe_writer_credentials

    source = Path(__file__).resolve().parents[1]
    script = """import json,os,sys
from pathlib import Path
sys.path[:0]=[sys.argv[1]+'/src',sys.argv[1]]
from synapse_channel.core.protected_write_descriptor_filter import (
 install_protected_writer_descriptor_filter,
)
from tools.protected_runtime_probe.runtime import wait_probe_writer_release
install_protected_writer_descriptor_filter(expected_abi='linux-x86_64')
print(json.dumps({'pid':os.getpid()}),flush=True)
try:
 wait_probe_writer_release(0)
except ValueError:
 print('refused',flush=True)
else:
 (Path(sys.argv[2])/'marker').write_text('released')
 print('released',flush=True)
"""
    command = [
        "/usr/bin/bwrap",
        "--die-with-parent",
        "--unshare-user",
        "--ro-bind",
        "/",
        "/",
        "--bind",
        str(tmp_path),
        str(tmp_path),
        "--cap-drop",
        "ALL",
    ]
    command.extend((sys.executable, "-I", "-B", "-c", script, str(source), str(tmp_path)))
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert process.stdout is not None and process.stdin is not None
        readiness = process.stdout.readline()
        if not readiness:
            out, err = process.communicate(timeout=5)
            raise AssertionError((process.returncode, out.decode(), err.decode()))
        target = json.loads(readiness)["pid"]
        assert type(target) is int and target > 0
        assert not (tmp_path / "marker").exists()
        uid = 902 + (case == "uid-mismatch")
        gid = 902 + (case == "gid-mismatch")
        status = (Path("/proc") / str(target) / "status").read_text().splitlines()
        groups = next(
            line.partition(":")[2].split() for line in status if line.startswith("Groups:")
        )
        assert groups and sorted(map(int, groups)) == sorted(os.getgroups())
        with pytest.raises(ValueError, match="credentials differ"):
            observe_probe_writer_credentials(target, expected_uid=uid, expected_gid=gid)
        assert not (tmp_path / "marker").exists()
        release = b"\x00"
        out, err = process.communicate(input=release, timeout=20)
        assert process.returncode == 0, err.decode()
        assert out.strip() == b"refused"
        assert not (tmp_path / "marker").exists()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate()


def test_owned_root_path_modules_use_no_nss_imports() -> None:
    """Keep the packet's owned pre-drop modules on the local-file account path."""
    source = Path(__file__).resolve().parents[1]
    paths = sorted((source / "tools/protected_runtime_probe").glob("*.py"))
    paths += sorted((source / "src/synapse_channel/core").glob("protected_write*.py"))
    assert paths
    forbidden = {"pwd", "grp", "spwd"}
    failures = []
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.split(".")[0] in forbidden for name in names):
                failures.append((path.relative_to(source).as_posix(), node.lineno))
    assert not failures


def test_named_role_resolution_uses_real_local_files_without_nss(tmp_path: Path) -> None:
    """Resolve all four disabled names with an unavailable NSS backend."""
    if not _native_profile_or_refusal(tmp_path):
        return
    result = _boundary(tmp_path, parent_change="nss-unavailable")
    assert result["phase"] == "child_refused"
    assert result["resolved_local_roles"] == [
        {"name": name, "uid": 900 + index, "gid": 900 + index}
        for index, name in enumerate(
            ("synapse-authority", "synapse-operator", "synapse-writer", "synapse-client")
        )
    ]
    assert result["account_backend_modules_loaded"] == []


@pytest.mark.parametrize(
    "field",
    [
        "Uid",
        "Gid",
        "Groups",
        "CapInh",
        "CapPrm",
        "CapEff",
        "CapBnd",
        "CapAmb",
        "NoNewPrivs",
        "Seccomp",
        "Seccomp_filters",
    ],
)
def test_writer_status_conditions_change_independently_from_real_proc_bytes(field: str) -> None:
    """Changing one parsed input clause changes only that condition's result.

    These are parser inputs derived from real proc bytes, not claims that the
    kernel or a child changed credentials. The observer's real process refusal
    remains a separate integration test; no input bytes can release that child.
    """
    from tools.protected_runtime_probe.roles import compare_probe_writer_status

    if sys.platform != "linux":
        with pytest.raises(OSError, match="native Linux"):
            from tools.protected_runtime_probe.roles import observe_probe_writer_credentials

            observe_probe_writer_credentials(1, expected_uid=902, expected_gid=902)
        return
    raw = Path("/proc/self/status").read_bytes()
    observed, baseline = compare_probe_writer_status(
        raw, expected_uid=os.getuid(), expected_gid=os.getgid()
    )
    if field in {"Uid", "Gid"}:
        numbers = observed[field].split()
        assert len(numbers) == 4
        numbers[-1] = str(int(numbers[-1]) + 1)
        value = " ".join(numbers)
    elif field == "Groups":
        value = "" if observed[field] else str(os.getgid())
    elif field.startswith("Cap"):
        value = "0000000000000001" if baseline[field] else "0000000000000000"
    else:
        accepted = {"NoNewPrivs": "1", "Seccomp": "2", "Seccomp_filters": "1"}
        value = "0" if baseline[field] else accepted[field]
    lines = raw.splitlines(keepends=True)
    position = next(
        index for index, line in enumerate(lines) if line.startswith(field.encode() + b":")
    )
    changed_lines = lines.copy()
    changed_lines[position] = field.encode() + b":\t" + value.encode() + b"\n"
    assert sum(first != second for first, second in zip(lines, changed_lines, strict=True)) == 1
    changed, checks = compare_probe_writer_status(
        b"".join(changed_lines), expected_uid=os.getuid(), expected_gid=os.getgid()
    )
    assert changed[field] == value
    assert checks[field] is not baseline[field]
    assert {name for name in baseline if checks[name] != baseline[name]} == {field}


@pytest.mark.parametrize("field", ["uid", "gid"])
@pytest.mark.parametrize("value", [0, 1000, -1, True, 2**32 - 1])
def test_writer_observer_refuses_invalid_enrolled_identity_before_proc(
    field: str, value: int
) -> None:
    """A nonexistent process cannot mask the dedicated-identity argument refusal."""
    from tools.protected_runtime_probe.roles import observe_probe_writer_credentials

    if sys.platform != "linux":
        with pytest.raises(OSError, match="native Linux"):
            observe_probe_writer_credentials(1, expected_uid=902, expected_gid=902)
        return
    uid, gid = (value, 902) if field == "uid" else (902, value)
    with pytest.raises(ValueError, match="numeric identity"):
        observe_probe_writer_credentials(2**31 - 1, expected_uid=uid, expected_gid=gid)


@pytest.mark.parametrize(
    "case,message",
    [
        ("nonbytes", "status bytes"),
        ("boolean-target", "observed identity"),
        ("negative-target", "observed identity"),
        ("sentinel-target", "observed identity"),
        ("oversized", "exceeds byte bound"),
        ("duplicate-required", "repeated"),
        ("missing-required", "incomplete"),
        ("nondecimal-value", "not decimal"),
        ("out-of-range-value", "kernel bounds"),
        ("nonascii-status", "ascii"),
    ],
)
def test_public_writer_status_rejects_invalid_input_domain(case: str, message: str) -> None:
    """Refuse malformed parser inputs derived from the actual process status."""
    from tools.protected_runtime_probe.roles import compare_probe_writer_status

    if sys.platform != "linux":
        return
    raw = Path("/proc/self/status").read_bytes()
    uid, gid = os.geteuid(), os.getegid()
    observed, checks = compare_probe_writer_status(raw, expected_uid=uid, expected_gid=gid)
    assert checks["Uid"] and checks["Gid"]
    selected: bytes | bytearray = raw
    if case == "nonbytes":
        selected = bytearray(raw)
    elif case == "boolean-target":
        uid = True
    elif case == "negative-target":
        gid = -1
    elif case == "sentinel-target":
        uid = 2**32 - 1
    elif case == "oversized":
        selected += b"x" * (16385 - len(raw))
    elif case == "duplicate-required":
        selected += b"Uid:\t" + observed["Uid"].encode() + b"\n"
    elif case == "missing-required":
        selected = b"\n".join(row for row in raw.split(b"\n") if not row.startswith(b"NoNewPrivs:"))
    elif case in {"nondecimal-value", "out-of-range-value"}:
        field, value = (
            (b"Uid", b"not-decimal") if case == "nondecimal-value" else (b"Gid", b"4294967295")
        )
        selected = b"\n".join(
            field + b":\t" + value if row.startswith(field + b":") else row
            for row in raw.split(b"\n")
        )
    else:
        selected += b"Ignored:\t\xff\n"
    with pytest.raises(ValueError, match=message):
        compare_probe_writer_status(cast(bytes, selected), expected_uid=uid, expected_gid=gid)


def test_public_role_resolver_rejects_unknown_name(tmp_path: Path) -> None:
    """Unknown names refuse before any local account file can be read."""
    from tools.protected_runtime_probe.roles import resolve_role_identity

    if not _native_profile_or_refusal(tmp_path):
        return
    with pytest.raises(ValueError, match="invalid prerequisite target role"):
        resolve_role_identity("not-an-enrolled-role")


@pytest.mark.parametrize("case", ["mode", "bytebound", "malformed", "duplicate"])
def test_public_role_resolver_refuses_unsafe_actual_account_files(
    tmp_path: Path, case: str
) -> None:
    """Only private namespace account inputs change; host account files remain read-only."""
    from fixtures.protected_runtime_role_boundary import run_mapped_public_refusal

    if not _native_profile_or_refusal(tmp_path):
        return
    expected = {
        "mode": "not root controlled",
        "bytebound": "exceeds byte bound",
        "malformed": "malformed or repeated",
        "duplicate": "malformed or repeated",
    }[case]
    entry = f"""from tools.protected_runtime_probe.roles import resolve_role_identity
try:resolve_role_identity('synapse-writer')
except ValueError as error:
 assert {expected!r} in str(error)
 print(json.dumps({{'refused':True,'case':{case!r}}},sort_keys=True))
else:raise AssertionError('unsafe account input was accepted')
"""
    assert run_mapped_public_refusal(
        Path(__file__).resolve().parents[1], tmp_path, entry, account_case=case
    ) == {"refused": True, "case": case}


@pytest.mark.parametrize("case", ["threaded", "capability"])
def test_public_identity_drop_refuses_actual_thread_or_capability_state(
    tmp_path: Path, case: str
) -> None:
    """Refuse before changing numeric identities, retaining real kernel error semantics."""
    from fixtures.protected_runtime_role_boundary import run_mapped_public_refusal

    if not _native_profile_or_refusal(tmp_path):
        return
    entry = """import errno,threading
from tools.protected_runtime_probe.roles import drop_enrolled_identity
stopped=threading.Event();thread=None
try:
 if CASE=='threaded':
  thread=threading.Thread(target=stopped.wait);thread.start()
  assert len(tuple(Path('/proc/self/task').iterdir()))>1
 try:drop_enrolled_identity(900,901)
 except (ValueError,OSError) as error:
  if CASE=='threaded':assert 'single thread' in str(error)
  else:
   assert 'capability bound refused' in str(error)
   assert isinstance(error,OSError) and error.errno==errno.EPERM
  print(json.dumps({'refused':True,'case':CASE},sort_keys=True))
 else:raise AssertionError('invalid credential state was accepted')
finally:
 stopped.set()
 if thread is not None:
  thread.join(timeout=3);assert not thread.is_alive()
""".replace("CASE", repr(case))
    assert run_mapped_public_refusal(
        Path(__file__).resolve().parents[1],
        tmp_path,
        entry,
        drop_capabilities=case == "capability",
    ) == {"refused": True, "case": case}
