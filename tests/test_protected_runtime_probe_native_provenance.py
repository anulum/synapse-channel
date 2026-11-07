# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — native metadata command output bounds and process lifetime
"""Exercise actual metadata subprocess bounds, deadlines and owned-child reaping."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tools.protected_runtime_probe.native_provenance import (
    SYSTEM_PACKAGE_TREE,
    NativePackageTree,
    collect_native_metadata_process,
    load_native_wheel,
    run_native_metadata_command,
    verify_dpkg_native_file,
)


@pytest.mark.parametrize("case", ["stdout", "stderr", "deadline", "closed-streams-deadline"])
def test_metadata_commands_bound_actual_output_and_reap_the_owned_child(
    tmp_path: Path, case: str
) -> None:
    """Excessive native-tool output and elapsed deadlines leave no owned live process."""
    if not Path("/usr/bin/python3.12").is_file():
        with pytest.raises(OSError):
            run_native_metadata_command("/usr/bin/python3.12", ("-I", "-B", "-S", "-c", "pass"))
        return
    pid_file = tmp_path / "owned-child.pid"
    body = (
        "import os,time;from pathlib import Path;Path("
        + repr(str(pid_file))
        + ").write_text(str(os.getpid()));"
    )
    deadline = case.endswith("deadline")
    if deadline:
        body += "os.close(1);os.close(2);" if case == "closed-streams-deadline" else ""
        body += "time.sleep(30)"
    else:
        descriptor, size = (1, 2 * 1024 * 1024 + 1) if case == "stdout" else (2, 65537)
        body += f"os.write({descriptor},b'x'*{size});time.sleep(30)"
    with pytest.raises(TimeoutError if deadline else ValueError):
        run_native_metadata_command(
            "/usr/bin/python3.12", ("-I", "-B", "-S", "-c", body), timeout=3
        )
    pid = int(pid_file.read_text())
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


@pytest.mark.parametrize(
    ("arguments", "timeout"),
    [
        ((), 0),
        ((), 31),
        ((), float("inf")),
        ((), float("nan")),
        (("x",) * 65, 1),
        (("x" * 65537,), 1),
    ],
)
def test_metadata_command_refuses_invalid_limits_before_launch(
    arguments: tuple[str, ...], timeout: float
) -> None:
    """Public command limits reject nonfinite deadlines and oversized argument lists."""
    with pytest.raises(ValueError, match="bounds are invalid"):
        run_native_metadata_command("/usr/bin/python3.12", arguments, timeout=timeout)


def test_metadata_command_refuses_an_executable_outside_its_fixed_set() -> None:
    """A shell cannot be selected as a native metadata tool."""
    with pytest.raises(ValueError, match="outside the fixed tool set"):
        run_native_metadata_command("/bin/sh", ("-c", "exit 0"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("document", []),
        ("schema", "unsupported"),
        ("native_abi", "unsupported"),
        ("websockets", None),
        ("websockets.version", "0"),
        ("websockets.filename", "different.whl"),
        ("websockets.sha256", 1),
        ("websockets.sha256", "invalid"),
        ("websockets.size_bytes", True),
        ("websockets.size_bytes", 0),
        ("websockets.size_bytes", 8 * 1024 * 1024 + 1),
    ],
)
def test_offline_wheel_capture_refuses_an_invalid_profile(
    tmp_path: Path, field: str, value: object
) -> None:
    """Real lock files with unsupported schemas or malformed pins refuse before wheel I/O."""
    source = Path(__file__).resolve().parents[1]
    record = json.loads(
        (source / "tools/protected_runtime_probe/native-dependencies.lock.json").read_bytes()
    )
    wheel = tmp_path / record["websockets"]["filename"]
    if field == "document":
        record = value
    elif field.startswith("websockets."):
        record["websockets"][field.removeprefix("websockets.")] = value
    else:
        record[field] = value
    lock = tmp_path / "native-dependencies.lock.json"
    lock.write_text(json.dumps(record))
    if os.name != "posix":
        with pytest.raises(OSError, match="requires native POSIX descriptors"):
            load_native_wheel(lock, wheel)
        return
    with pytest.raises(ValueError, match="malformed or unsupported"):
        load_native_wheel(lock, wheel)


@dataclass(frozen=True)
class InstalledPackageCopy:
    """Actual system bytes and dpkg metadata copied into an explicitly separate tree."""

    source: Path
    tree: NativePackageTree
    package: str
    recorded_path: str


def _copy_package_metadata(package: str, root: Path) -> None:
    """Copy actual dpkg format/architecture and one installed package's public records."""
    database = root / "var/lib/dpkg"
    (database / "info").mkdir(parents=True)
    for relative in ("arch", "info/format"):
        installed = Path("/var/lib/dpkg") / relative
        if installed.is_file():
            (database / relative).write_bytes(installed.read_bytes())
    for arguments, target in (
        (("--status", package), database / "status"),
        (("--listfiles", package), database / "info" / (package + ".list")),
        (("--control-show", package, "md5sums"), database / "info" / (package + ".md5sums")),
    ):
        code, raw = run_native_metadata_command("/usr/bin/dpkg-query", arguments)
        assert code == 0
        target.write_bytes(raw)


@pytest.fixture(params=["/usr/bin/dpkg-query", "/usr/lib/x86_64-linux-gnu/libc.so.6"])
def copied_package_tree(
    request: pytest.FixtureRequest, tmp_path: Path
) -> InstalledPackageCopy | None:
    """Copy a real installed package's status/list/md5sums without changing the host."""
    if not Path("/usr/bin/dpkg-query").is_file():
        return None
    name = request.param
    assert isinstance(name, str)
    original = Path(name).resolve(strict=True)
    verified = verify_dpkg_native_file(original, original.read_bytes())
    package, recorded = verified["package"], verified["package_record_path"]
    assert isinstance(package, str) and isinstance(recorded, str)
    root = tmp_path / "host"
    source = root / original.relative_to("/")
    source.parent.mkdir(parents=True)
    shutil.copyfile(original, source)
    source.chmod(0o400)
    _copy_package_metadata(package, root)
    if original.is_relative_to("/usr/lib"):
        (root / "lib").symlink_to("usr/lib", target_is_directory=True)
    metadata = source.stat()
    return InstalledPackageCopy(
        source, NativePackageTree(root, (metadata.st_uid, metadata.st_gid)), package, recorded
    )


def _native_package_tool_or_refusal(case: InstalledPackageCopy | None) -> bool:
    """Exercise the real unavailable-tool refusal where dpkg is absent."""
    if case is not None:
        return True
    with pytest.raises(OSError):
        run_native_metadata_command("/usr/bin/dpkg-query", ("--version",))
    return False


def test_actual_dpkg_accepts_a_labelled_copy_of_real_package_metadata(
    copied_package_tree: InstalledPackageCopy | None,
) -> None:
    """Actual dpkg queries bind copied native bytes to the declared alternate context."""
    if not _native_package_tool_or_refusal(copied_package_tree):
        return
    assert copied_package_tree is not None
    case = copied_package_tree
    raw = case.source.read_bytes()
    result = verify_dpkg_native_file(case.source, raw, tree=case.tree)
    verified_at = result["verified_at"]
    assert isinstance(verified_at, str)
    stamp = datetime.fromisoformat(verified_at)
    assert stamp.tzinfo is UTC
    assert abs((datetime.now(UTC) - stamp).total_seconds()) < 30
    assert result["package"] == case.package and result["dpkg_md5_verified"] is True
    assert result["package_record_path"] == case.recorded_path
    assert result["source_context"] == "explicit-tree"
    assert result["source_root"] == str(case.tree.root)
    assert result["expected_owner_uid"] == os.getuid()
    assert result["expected_owner_gid"] == case.source.stat().st_gid


@pytest.mark.parametrize(
    "case_name", ["no-owner", "missing-md5", "changed-file", "not-installed", "broken-status"]
)
def test_actual_dpkg_refuses_inconsistent_metadata_or_changed_installed_bytes(
    copied_package_tree: InstalledPackageCopy | None, case_name: str
) -> None:
    """Real dpkg parses each changed private metadata tree and the verifier refuses it."""
    if not _native_package_tool_or_refusal(copied_package_tree):
        return
    assert copied_package_tree is not None
    case = copied_package_tree
    database = case.tree.root / "var/lib/dpkg"
    if case_name == "no-owner":
        path = database / "info" / (case.package + ".list")
        path.write_text("/.\n")
        message = "no verifiable package owner"
    elif case_name == "missing-md5":
        path = database / "info" / (case.package + ".md5sums")
        path.write_text("")
        message = "dpkg checksum"
    elif case_name == "changed-file":
        case.source.chmod(0o600)
        case.source.write_bytes(case.source.read_bytes() + b"\n")
        case.source.chmod(0o400)
        message = "dpkg checksum"
    elif case_name == "not-installed":
        path = database / "status"
        path.write_text(
            path.read_text().replace(
                "Status: install ok installed", "Status: deinstall ok config-files"
            )
        )
        message = "not currently installed"
    else:
        (database / "status").write_text("not a valid dpkg status record\n")
        message = "metadata is unavailable"
    with pytest.raises(ValueError, match=message):
        verify_dpkg_native_file(case.source, case.source.read_bytes(), tree=case.tree)


@pytest.mark.parametrize(
    ("missing", "already_exited"),
    [
        ("stdout", False),
        ("stderr", False),
        ("both", False),
        ("stdout", True),
        ("stderr", True),
        ("both", True),
    ],
)
@pytest.mark.asyncio
async def test_metadata_collector_requires_real_pipes_and_reaps_its_child(
    missing: str, already_exited: bool
) -> None:
    """Actual children with incomplete IO ownership are stopped and reaped on refusal."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-B",
        "-c",
        "pass" if already_exited else "import time;time.sleep(30)",
        stdout=asyncio.subprocess.DEVNULL
        if missing in {"stdout", "both"}
        else asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL
        if missing in {"stderr", "both"}
        else asyncio.subprocess.PIPE,
    )
    if already_exited:
        await process.wait()
    with pytest.raises(ValueError, match="output pipes are unavailable"):
        await collect_native_metadata_process(process)
    assert process.returncode is not None
    assert await process.wait() == process.returncode
    if os.name == "posix":
        with pytest.raises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)


@pytest.mark.asyncio
async def test_metadata_collector_reaps_the_actual_child_on_an_invalid_deadline() -> None:
    """An invalid collector request still retires the real child whose ownership transferred."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-B",
        "-c",
        "import time;time.sleep(30)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    with pytest.raises(ValueError, match="bounds are invalid"):
        await collect_native_metadata_process(process, timeout=0)
    assert process.returncode is not None
    assert await process.wait() == process.returncode


def test_default_native_package_context_binds_the_actual_system_root() -> None:
    """The default context imports on every host and preserves actual Linux provenance."""
    context = NativePackageTree()
    assert context.root.is_absolute()
    assert context == SYSTEM_PACKAGE_TREE
    if sys.platform != "linux":
        return
    assert context == NativePackageTree(Path("/"))
    assert SYSTEM_PACKAGE_TREE.root == Path("/")
    source = Path("/usr/bin/dpkg-query")
    if not source.is_file():
        with pytest.raises(OSError):
            run_native_metadata_command(str(source), ("--version",))
        return
    result = verify_dpkg_native_file(source, source.read_bytes())
    assert result["source_root"] == "/"
    assert result["source_context"] == "system"


@pytest.mark.parametrize("root", [Path("relative"), Path("/usr/../var")])
def test_native_package_context_refuses_ambiguous_relative_roots(root: Path) -> None:
    """Filesystem source and metadata queries must share an unambiguous absolute root."""
    with pytest.raises(ValueError, match="absolute canonical root"):
        NativePackageTree(root)


@pytest.mark.parametrize(
    "problem", ["uid", "gid", "writable", "directory", "fifo", "unreadable", "capture", "escape"]
)
def test_native_package_refuses_actual_file_ownership_type_and_content_changes(
    copied_package_tree: InstalledPackageCopy | None, problem: str
) -> None:
    """Actual filesystem changes cannot retain the previously accepted package provenance."""
    if not _native_package_tool_or_refusal(copied_package_tree):
        return
    assert copied_package_tree is not None
    case = copied_package_tree
    raw, tree, source = case.source.read_bytes(), case.tree, case.source
    exception: type[Exception] = ValueError
    message: str | None = "protected system ownership"
    if problem == "uid":
        tree = NativePackageTree(tree.root, (tree.owner[0] + 1, tree.owner[1]))
    elif problem == "gid":
        tree = NativePackageTree(tree.root, (tree.owner[0], tree.owner[1] + 1))
    elif problem == "writable":
        source.chmod(0o622)
    elif problem in {"directory", "fifo"}:
        source.unlink()
        if problem == "directory":
            source.mkdir()
        else:
            os.mkfifo(source, 0o600)
    elif problem == "unreadable":
        source.chmod(0)
        exception, message = PermissionError, None
    elif problem == "capture":
        raw += b"changed"
        message = "differs from its installed source"
    else:
        original = Path("/") / source.relative_to(tree.root)
        source.unlink()
        source.symlink_to(original)
        tree = NativePackageTree(tree.root)
        message = "outside its declared package tree"
    try:
        with pytest.raises(exception, match=message):
            verify_dpkg_native_file(source, raw, tree=tree)
    finally:
        if problem == "unreadable":
            source.chmod(0o400)


def test_actual_dpkg_refuses_multiple_package_claims(
    copied_package_tree: InstalledPackageCopy | None,
) -> None:
    """Actual dpkg reports a deliberately conflicting private ownership database."""
    if not _native_package_tool_or_refusal(copied_package_tree):
        return
    assert copied_package_tree is not None
    case = copied_package_tree
    code, status = run_native_metadata_command("/usr/bin/dpkg-query", ("--status", "bash"))
    assert code == 0 and case.package != "bash"
    database = case.tree.root / "var/lib/dpkg"
    with (database / "status").open("ab") as stream:
        stream.write(b"\n" + status)
    (database / "info/bash.list").write_text("/" + case.recorded_path + "\n")
    with pytest.raises(ValueError, match="ownership is ambiguous"):
        verify_dpkg_native_file(case.source, case.source.read_bytes(), tree=case.tree)


@pytest.mark.parametrize("alias", ["absent", "different-object"])
def test_actual_dpkg_copy_does_not_require_an_unverified_merged_usr_alias(
    copied_package_tree: InstalledPackageCopy | None, alias: str
) -> None:
    """Only an alias of the exact actual object can contribute a legacy lookup path."""
    if not _native_package_tool_or_refusal(copied_package_tree):
        return
    assert copied_package_tree is not None
    case = copied_package_tree
    link = case.tree.root / "lib"
    if link.is_symlink():
        link.unlink()
    if alias == "different-object":
        other = case.tree.root / "other"
        other.mkdir()
        (other / case.source.name).write_bytes(b"another object")
        link.symlink_to(other, target_is_directory=True)
    result = verify_dpkg_native_file(case.source, case.source.read_bytes(), tree=case.tree)
    assert result["package_record_path"] == case.recorded_path
    assert result["dpkg_md5_verified"] is True
