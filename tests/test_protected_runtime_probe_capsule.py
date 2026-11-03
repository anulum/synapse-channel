# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actual prerequisite byte custody and privilege refusals
"""Exercise public packet capture, complete preparation and real unprivileged refusal."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import shlex
import socket
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from tools.protected_runtime_probe import capsule
from tools.protected_runtime_probe.capsule import (
    CapsuleManifest,
    CapturedCapsule,
    FilePin,
    capture_capsule,
    decode_manifest,
    read_relative_file,
)
from tools.protected_runtime_probe.prepare import prepare_packet


def _fixture(root: Path) -> tuple[str, bytes]:
    """Use real codec source bytes in a small codec-only, non-executable fixture."""
    raw = Path(capsule.__file__).read_bytes()
    name = "app/source.py"
    (root / "app").mkdir()
    (root / name).write_bytes(raw)
    manifest = CapsuleManifest(
        "a" * 40,
        "/media/anulum/GOTM/prerequisite-test-only",
        (FilePin(name, hashlib.sha256(raw).hexdigest(), len(raw)),),
    ).canonical_bytes()
    (root / "manifest.json").write_bytes(manifest)
    return hashlib.sha256(manifest).hexdigest(), raw


def test_manifest_is_closed_canonical_and_bounded(tmp_path: Path) -> None:
    """A real manifest round-trip refuses ambiguous keys, extensions and traversal."""
    digest, _raw = _fixture(tmp_path)
    encoded = (tmp_path / "manifest.json").read_bytes()
    assert hashlib.sha256(decode_manifest(encoded).canonical_bytes()).hexdigest() == digest
    duplicate = encoded.replace(b'"files":', b'"schema":"wrong","files":', 1)
    with pytest.raises(ValueError, match="duplicate"):
        decode_manifest(duplicate)
    record = json.loads(encoded)
    record["extra"] = "not a permitted inventory field"
    with pytest.raises(ValueError, match="shape"):
        decode_manifest(json.dumps(record, sort_keys=True, separators=(",", ":")).encode())
    del record["extra"]
    record["files"][0]["path"] = "app/../outside"
    with pytest.raises(ValueError, match="relative path"):
        decode_manifest(json.dumps(record, sort_keys=True, separators=(",", ":")).encode())


@pytest.mark.parametrize(
    "change",
    [
        "empty",
        "oversized",
        "scope",
        "backing",
        "digest",
        "size",
        "source",
        "schema",
        "abi",
        "file-count",
        "file-type",
        "row-type",
        "row-keys",
        "noncanonical",
        "duplicate",
        "aggregate",
        "guest-uuid",
        "guest-uuid-type",
        "guest-uuid-missing",
    ],
)
def test_unqualified_manifests_cannot_authorize_capsule_capture(
    tmp_path: Path, change: str
) -> None:
    """Reject changed approval scope, native profile, exact pins and declared byte bounds."""
    _fixture(tmp_path)
    raw = (tmp_path / "manifest.json").read_bytes()
    record = json.loads(raw)
    if change == "empty":
        raw = b""
    elif change == "oversized":
        raw = b"x" * (2 * 1024 * 1024 + 1)
    elif change == "noncanonical":
        raw = json.dumps(record, indent=2).encode()
    else:
        if change == "scope":
            record["files"][0]["path"] = "unapproved/source.py"
        elif change == "backing":
            record["backing_parent"] = "/tmp/not-Samsung"
        elif change == "digest":
            record["files"][0]["sha256"] = "not-a-digest"
        elif change == "size":
            record["files"][0]["size_bytes"] = True
        elif change == "guest-uuid":
            record["guest_uuid"] = "not-an-enrolled-guest"
        elif change == "guest-uuid-type":
            record["guest_uuid"] = True
        elif change == "guest-uuid-missing":
            del record["guest_uuid"]
        elif change == "source":
            record["source_head"] = "not-a-commit"
        elif change in {"schema", "abi"}:
            record["native_abi" if change == "abi" else "schema"] = "unapproved-profile"
        elif change == "file-count":
            record["files"] = []
        elif change == "file-type":
            record["files"] = {}
        elif change == "row-type":
            record["files"] = [None]
        elif change == "row-keys":
            del record["files"][0]["sha256"]
        elif change == "duplicate":
            record["files"] *= 2
        else:
            record["files"] = [
                {"path": f"app/payload{index}", "sha256": "a" * 64, "size_bytes": 64 * 1024 * 1024}
                for index in range(5)
            ]
        raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ValueError):
        decode_manifest(raw)


def test_programmatic_inventory_requires_immutable_typed_pins() -> None:
    """Typed callers cannot bypass the wire codec's exact immutable inventory rule."""
    with pytest.raises(ValueError, match="payload count"):
        CapsuleManifest("a" * 40, "/media/anulum/GOTM/test-only", ())
    with pytest.raises(ValueError, match="immutable file pins"):
        CapsuleManifest(
            "a" * 40, "/media/anulum/GOTM/test-only", cast(tuple[FilePin, ...], (None,))
        )


def test_captured_bytes_survive_source_replacement_and_refuse_a_new_capture(tmp_path: Path) -> None:
    """Approval pins actual bytes; later caller mutations cannot change the captured view."""
    digest, raw = _fixture(tmp_path)
    if os.name != "posix":
        with pytest.raises(OSError, match="POSIX"):
            capture_capsule(tmp_path, digest)
        return
    captured = capture_capsule(tmp_path, digest)
    assert captured.content["app/source.py"] == raw
    (tmp_path / "app/source.py").write_bytes(b"replaced after approval\n")
    assert captured.content["app/source.py"] == raw
    with pytest.raises(ValueError, match="payload differs"):
        capture_capsule(tmp_path, digest)
    with pytest.raises(ValueError, match="independent approval"):
        capture_capsule(tmp_path, "0" * 64)


@pytest.mark.parametrize("object_kind", ["leaf-link", "parent-link", "leaf-hardlink", "fifo"])
def test_capture_refuses_real_alternate_objects(tmp_path: Path, object_kind: str) -> None:
    """Identical linked bytes and nonblocking FIFOs cannot replace approved regular entries."""
    digest, raw = _fixture(tmp_path)
    if os.name != "posix":
        with pytest.raises(OSError, match="POSIX"):
            capture_capsule(tmp_path, digest)
        return
    leaf = tmp_path / "app/source.py"
    if object_kind == "parent-link":
        (tmp_path / "app").rename(tmp_path / "original")
        (tmp_path / "app").symlink_to("original", target_is_directory=True)
    else:
        leaf.unlink()
        if object_kind == "leaf-link":
            (tmp_path / "identical.py").write_bytes(raw)
            leaf.symlink_to("../identical.py")
        elif object_kind == "leaf-hardlink":
            (tmp_path / "identical.py").write_bytes(raw)
            os.link(tmp_path / "identical.py", leaf)
        else:
            os.mkfifo(leaf)
    with pytest.raises((ValueError, OSError)):
        capture_capsule(tmp_path, digest)


def test_relative_read_retains_directory_identity_and_bounds_bytes(tmp_path: Path) -> None:
    """A borrowed FD survives pathname replacement and never reads a larger file."""
    if os.name != "posix":
        with pytest.raises(OSError, match="POSIX"):
            read_relative_file(0, "file", 10)
        return
    original = tmp_path / "original"
    original.mkdir()
    (original / "file").write_bytes(b"retained")
    descriptor = os.open(original, os.O_RDONLY | os.O_DIRECTORY)
    try:
        original.rename(tmp_path / "moved")
        original.mkdir()
        (original / "file").write_bytes(b"replacement")
        assert read_relative_file(descriptor, "file", 8) == b"retained"
        with pytest.raises(ValueError, match="bounded regular"):
            read_relative_file(descriptor, "file", 7)
        with pytest.raises(ValueError, match="relative path"):
            read_relative_file(descriptor, "../file", 8)
    finally:
        os.close(descriptor)


def test_cache_links_are_copied_but_never_accepted_as_staged_files(tmp_path: Path) -> None:
    """A native package cache link can supply bytes without becoming staged custody."""
    if os.name != "posix":
        with pytest.raises(OSError, match="POSIX"):
            read_relative_file(0, "file", 8)
        return
    (tmp_path / "source").write_bytes(b"source")
    os.link(tmp_path / "source", tmp_path / "cache-link")
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert read_relative_file(descriptor, "source", 6) == b"source"
        with pytest.raises(ValueError, match="one link"):
            read_relative_file(descriptor, "source", 6, require_single_link=True)
    finally:
        os.close(descriptor)


def test_zero_size_native_metadata_cannot_bypass_a_zero_read_budget(tmp_path: Path) -> None:
    """Linux proc regular files report size zero but still require a bounded byte read."""
    if os.name != "posix":
        with pytest.raises(OSError, match="POSIX"):
            read_relative_file(0, "status", 0)
        return
    directory = Path("/proc/self") if sys.platform == "linux" else tmp_path
    if sys.platform != "linux":
        (directory / "status").write_bytes(b"nonempty native file")
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError):
            read_relative_file(descriptor, "status", 0)
        with pytest.raises(ValueError, match="enrollment"):
            read_relative_file(descriptor, "status", -1)
        with pytest.raises(ValueError, match="enrollment"):
            read_relative_file(descriptor, "status", 0, require_single_link=cast(bool, 1))
    finally:
        os.close(descriptor)
    with pytest.raises(ValueError, match="digest"):
        capture_capsule(tmp_path, "unapproved")


def test_manual_captured_record_cannot_introduce_unpinned_privileged_write_paths(
    tmp_path: Path,
) -> None:
    """The public snapshot record rejects traversal scope and replacement bytes itself."""
    digest, raw = _fixture(tmp_path)
    manifest = decode_manifest((tmp_path / "manifest.json").read_bytes())
    with pytest.raises(ValueError, match="payload scope"):
        CapturedCapsule(manifest, digest, {"../outside": raw})
    with pytest.raises(ValueError, match="payload bytes"):
        CapturedCapsule(manifest, digest, {"app/source.py": b"not the pinned bytes"})
    with pytest.raises(ValueError, match="manifest digest"):
        CapturedCapsule(manifest, "0" * 64, {"app/source.py": raw})


@pytest.mark.asyncio
async def test_real_unprivileged_entry_points_have_no_privileged_effects(tmp_path: Path) -> None:
    """Actual current non-root credentials refuse before account/cgroup/storage mutation."""
    if sys.platform != "linux":
        from tools.protected_runtime_probe.namespace_child import main
        from tools.protected_runtime_probe.runtime import capture_runtime_content

        with pytest.raises(OSError, match="native Linux"):
            main([])
        if os.name != "posix":
            with pytest.raises(OSError, match="native POSIX"):
                capture_runtime_content(tmp_path, tmp_path / "manifest", "0" * 64)
        return
    from tools.protected_runtime_probe.namespace_child import main
    from tools.protected_runtime_probe.resources import create_resource_scope
    from tools.protected_runtime_probe.roles import Role, drop_privileges, ensure_roles
    from tools.protected_runtime_probe.storage import create_backing

    if os.geteuid() == 0:
        raise RuntimeError("unprivileged negative controls must run without root")
    with pytest.raises(PermissionError, match="root"):
        await ensure_roles()
    with pytest.raises(PermissionError, match="root"):
        drop_privileges(Role("synapse-writer", 1, 1))
    with pytest.raises(PermissionError, match="root"):
        create_resource_scope("run-" + "0" * 16)
    with pytest.raises(PermissionError, match="root"):
        create_backing(tmp_path, "run-" + "0" * 16)
    with pytest.raises(PermissionError, match="root"):
        main([])
    assert not (tmp_path / ("run-" + "0" * 16)).exists()


@pytest.mark.asyncio
async def test_complete_packet_and_operator_gate_capture_exact_bootstrap(tmp_path: Path) -> None:
    """Real whole-source preparation and sealed loader reject changed bootstrap bytes."""
    source = Path(__file__).resolve().parents[1]
    if sys.platform != "linux" or platform.machine() != "x86_64" or sys.version_info[:2] != (3, 12):
        with pytest.raises(OSError, match="native Linux"):
            prepare_packet(source, tmp_path / "packet", tmp_path / "root-runs")
        return
    storage = Path("/media/anulum/GOTM")
    if not storage.is_mount() or not tmp_path.is_relative_to(storage):
        with pytest.raises(ValueError, match="Samsung"):
            await asyncio.to_thread(
                prepare_packet, source, tmp_path / "packet", storage / "prerequisite-test-only"
            )
        return
    # prepare_packet owns a synchronous CLI event loop; use its actual process entry.
    audited_cli = """import runpy,sys
observed=[]
def observe(event,arguments):
 if event=='subprocess.Popen':
  assert arguments[0]=='/usr/bin/git'
  assert arguments[3]=={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'}
  observed.append(True)
sys.addaudithook(observe)
try: runpy.run_module('tools.protected_runtime_probe.prepare',run_name='__main__')
except SystemExit as result:
 assert result.code==0
 assert observed==[True]
"""
    command = [
        sys.executable,
        "-B",
        "-c",
        audited_cli,
        "--source",
        str(source),
        "--bundle",
        str(tmp_path / "packet"),
        "--backing-parent",
        str(tmp_path / "root-runs"),
    ]
    process = await asyncio.create_subprocess_exec(
        *command, cwd=source, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        output, errors = await asyncio.wait_for(process.communicate(), 20)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, errors.decode()
    result = json.loads(output)
    assert "one fresh root backing directory" in result["retention"]
    assert "one readonly evidence bind" in result["retention"]
    captured = capture_capsule(tmp_path / "packet", result["manifest_sha256"])
    assert "app/synapse_channel/core/protected_write_kernel_execution.py" in captured.content
    assert "probe/protected_runtime_probe/controller.py" in captured.content
    assert "probe/protected_runtime_probe/reports.py" in captured.content
    assert "licenses/websockets.txt" in captured.content
    operator = shlex.split(result["operator_command"])
    assert operator[:4] == ["/usr/bin/python3.12", "-I", "-B", "-S"]
    assert "/usr/bin/sudo" not in operator
    assert result["guest_uuid"] == captured.manifest.guest_uuid
    # Run the exact reviewed root loader as our actual non-root process. Correct
    # bytes reach the real root-context refusal; tampered bytes never execute.
    initial = await asyncio.create_subprocess_exec(
        *operator, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _stdout, stderr = await asyncio.wait_for(initial.communicate(), 10)
    assert initial.returncode != 0 and b"trusted operator root context" in stderr
    isolated = await asyncio.create_subprocess_exec(
        "/usr/bin/bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-user",
        "--unshare-pid",
        "--uid",
        "0",
        "--gid",
        "0",
        "--cap-add",
        "ALL",
        "--ro-bind",
        "/",
        "/",
        "--ro-bind",
        str(storage),
        str(storage),
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--",
        *operator,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _stdout, stderr = await asyncio.wait_for(isolated.communicate(), 20)
        assert isolated.returncode != 0 and b"initial user namespace" in stderr, stderr.decode()
        assert b"create_backing" not in stderr and b"_run_captured" not in stderr
        assert b"NameError" not in stderr
        assert not (tmp_path / "root-runs").exists()
    finally:
        if isolated.returncode is None:
            isolated.kill()
            await isolated.wait()
    bootstrap = tmp_path / "packet/probe/protected_runtime_probe/bootstrap.py"
    marker = tmp_path / "unapproved-effect"
    bootstrap.chmod(0o600)
    bootstrap.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n")
    changed = await asyncio.create_subprocess_exec(
        *operator, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _stdout, stderr = await asyncio.wait_for(changed.communicate(), 10)
    assert changed.returncode != 0 and b"bootstrap approval differs" in stderr
    assert not marker.exists()


@pytest.mark.parametrize("kind", ["regular", "limit", "fifo", "directory", "symlink", "oversized"])
@pytest.mark.asyncio
async def test_effect_marker_reader_refuses_nonregular_files_without_waiting(
    tmp_path: Path, kind: str
) -> None:
    """Read real bounded content or promptly refuse other actual filesystem objects."""
    marker = tmp_path / "marker"
    content = b"x" * 4096 if kind == "limit" else b"runtime-marker\n"
    if kind == "fifo":
        os.mkfifo(marker)
    elif kind == "directory":
        marker.mkdir()
    elif kind == "symlink":
        target = tmp_path / "other"
        target.write_bytes(content)
        marker.symlink_to(target)
    else:
        marker.write_bytes(b"x" * 4097 if kind == "oversized" else content)
    source = Path(__file__).resolve().parents[1]
    code = "\n".join(
        (
            "import sys",
            "from tools.protected_runtime_probe.storage import read_effect_marker",
            "try:",
            "    raw = read_effect_marker(int(sys.argv[1]))",
            "except (OSError, ValueError):",
            "    print('refused')",
            "else:",
            "    print(raw.hex())",
        )
    )
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-B",
            "-c",
            code,
            str(directory),
            cwd=source,
            pass_fds=(directory,),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(child.communicate(), 5)
            assert child.returncode == 0, stderr.decode()
            expected = content.hex().encode() if kind in {"regular", "limit"} else b"refused"
            assert stdout.strip() == expected
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()
    finally:
        os.close(directory)


@pytest.mark.parametrize("case", ["complete", "cleanup-timeout", "child-failure", "retire-failure"])
@pytest.mark.asyncio
async def test_campaign_finalization_publishes_after_real_child_cleanup_interruption(
    tmp_path: Path, case: str
) -> None:
    """An actual child deadline still retires its endpoint and persists failure."""
    from tools.protected_runtime_probe.resources import finalize_probe_resources

    endpoint = tmp_path / "endpoint.sock"
    report = tmp_path / "result.json"
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    child: asyncio.subprocess.Process | None = None
    failures: list[str] = []
    entered_cleanup = False
    try:
        # Keep the actual sockaddr short while retaining the full Samsung path.
        listener.bind(f"/proc/self/fd/{directory}/endpoint.sock")
        listener.listen()
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-B",
            "-c",
            "import sys; print('ready', flush=True); sys.stdin.buffer.read(); "
            "sys.exit(int(sys.argv[1]))",
            "3" if case == "child-failure" else "0",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert child.stdout is not None
        assert await asyncio.wait_for(child.stdout.readline(), 5) == b"ready\n"
        owned_child = child

        async def cleanup() -> None:
            """Await this actual child, leaving its input open for the timeout case."""
            nonlocal entered_cleanup
            entered_cleanup = True
            if case != "cleanup-timeout":
                assert owned_child.stdin is not None
                owned_child.stdin.close()
            if await owned_child.wait() != 0:
                raise RuntimeError("ordinary child did not complete successfully")

        def retire() -> None:
            """Close the real endpoint and stop only the owned ordinary child."""
            listener.close()
            if owned_child.returncode is None:
                try:
                    owned_child.kill()
                except ProcessLookupError:
                    pass
            endpoint.unlink()

        def publish() -> None:
            """Persist the actual finalization outcome to an ordinary evidence file."""
            report.write_text(json.dumps({"passed": not failures, "failures": failures}))

        def failed(error: BaseException) -> None:
            """Retain the observed exception type without inventing role qualification."""
            failures.append(type(error).__name__)

        if case == "retire-failure":
            endpoint.unlink()
        completion = finalize_probe_resources(
            cleanup(), retire=retire, publish=publish, failed=failed
        )
        if case == "cleanup-timeout":
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(completion, 0.1)
        elif case == "child-failure":
            with pytest.raises(RuntimeError, match="ordinary child"):
                await completion
        elif case == "retire-failure":
            with pytest.raises(FileNotFoundError):
                await completion
        else:
            await completion
        assert entered_cleanup
        assert not endpoint.exists() and listener.fileno() == -1
        outcome = json.loads(report.read_text())
        assert outcome["passed"] is (case == "complete")
        assert (
            outcome["failures"]
            == {
                "complete": [],
                "cleanup-timeout": ["CancelledError"],
                "child-failure": ["RuntimeError"],
                "retire-failure": ["FileNotFoundError"],
            }[case]
        )
    finally:
        listener.close()
        if child is not None:
            if child.returncode is None:
                child.kill()
            await asyncio.wait_for(child.wait(), 5)
            if child.stdin is not None:
                child.stdin.close()
        endpoint.unlink(missing_ok=True)
        os.close(directory)


@pytest.mark.parametrize("kind", ["regular", "file-symlink", "directory-symlink", "fifo"])
def test_retained_backing_measurement_uses_actual_regular_files(tmp_path: Path, kind: str) -> None:
    """Measure real metadata or promptly refuse unsupported retained objects."""
    from tools.protected_runtime_probe.storage import measure_backing_tree

    if os.name != "posix":
        with pytest.raises(OSError, match="POSIX"):
            capsule.require_posix_descriptors(os.name)
        return

    backing = tmp_path / "backing"
    backing.mkdir()
    child = backing / "data"
    child.mkdir()
    marker = child / "marker"
    marker.write_bytes(b"finite retained effect\n")
    if kind == "fifo":
        os.mkfifo(backing / "other")
    elif kind == "file-symlink":
        (backing / "other").symlink_to(marker)
    elif kind == "directory-symlink":
        (backing / "other").symlink_to(child, target_is_directory=True)
    descriptor = os.open(backing, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if kind != "regular":
            with pytest.raises(ValueError, match="retained backing"):
                measure_backing_tree(descriptor)
            return
        result = measure_backing_tree(descriptor)
        metadata = [path.lstat() for path in (backing, child, marker)]
        assert result == {
            "size_bytes": sum(info.st_size for info in metadata),
            "allocated_bytes": sum(info.st_blocks * 512 for info in metadata),
            "files": 1,
            "directories": 2,
        }
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("kind", ["bound", "bound-executable", "unbound", "other-source"])
def test_retained_attempt_describes_the_actual_readonly_bind(tmp_path: Path, kind: str) -> None:
    """Real kernel mount and source identities define the retained recovery record."""
    source = Path(__file__).resolve().parents[1]
    if sys.platform != "linux" or platform.machine() != "x86_64" or sys.version_info[:2] != (3, 12):
        with pytest.raises(OSError, match="native Linux"):
            prepare_packet(source, tmp_path / "packet", tmp_path / "runs")
        return
    backing = tmp_path / "backing"
    backing.mkdir()
    report = backing / "report.json"
    report.write_text("{}")
    target = tmp_path / "evidence.json"
    target.write_bytes(b"")
    other = tmp_path / "other.json"
    other.write_text("{}")
    code = """import ctypes,json,os,sys
sys.path[:0]=[sys.argv[1]+'/src',sys.argv[1]]
if sys.argv[4]=='bound':
 libc=ctypes.CDLL(None,use_errno=True)
 libc.mount.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_char_p,ctypes.c_ulong,ctypes.c_char_p]
 libc.mount.restype=ctypes.c_int
 if libc.mount(None,os.fsencode(sys.argv[3]),None,4096|32|1|2|4|8,None)!=0:
  raise OSError(ctypes.get_errno(),'actual readonly noexec fixture remount failed')
from tools.protected_runtime_probe.storage import describe_retained_attempt
backing=os.open(sys.argv[2],os.O_RDONLY|os.O_DIRECTORY)
evidence=os.open('report.json',os.O_RDONLY,dir_fd=backing)
try:
 try: result=describe_retained_attempt(backing,evidence,__import__('pathlib').Path(sys.argv[3]))
 except ValueError as error: result={'error':str(error)}
 print(json.dumps(result))
finally:
 os.close(evidence);os.close(backing)
"""
    command = [
        "/usr/bin/bwrap",
        "--unshare-user",
        "--uid",
        "0",
        "--gid",
        "0",
        "--new-session",
        "--cap-add",
        "CAP_SYS_ADMIN",
        "--ro-bind",
        "/",
        "/",
        "--proc",
        "/proc",
    ]
    if kind != "unbound":
        command.extend(("--ro-bind", str(other if kind == "other-source" else report), str(target)))
    command.extend(
        (
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-c",
            code,
            str(source),
            str(backing),
            str(report if kind == "unbound" else target),
            kind,
        )
    )
    process = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    if kind != "bound":
        assert (
            "readonly bind" in result["error"]
            if kind in {"unbound", "bound-executable"}
            else "source identity" in result["error"]
        )
        return
    bind = result["evidence_bind"]
    assert bind["mount_id"] != bind["source_mount_id"]
    assert bind["target"] == str(target) and bind["source"] == str(report)
    assert (bind["source_device"], bind["source_inode"]) == (
        report.stat().st_dev,
        report.stat().st_ino,
    )
    ancestry = result["backing"]["ancestry"]
    assert ancestry[0]["path"] == str(backing) and ancestry[-1]["path"] == "/"
    assert ancestry[0]["uid"] == ancestry[0]["gid"] == 0
    assert all(type(row["root_controlled"]) is bool for row in ancestry)
    assert result["backing"] == {
        "path": str(backing),
        "device": backing.stat().st_dev,
        "inode": backing.stat().st_ino,
        "ancestry": ancestry,
    }
    retirement = result["retirement"]
    assert len(retirement) == 6 and "operator recovery" in result["reason"]
    assert "all campaign processes stopped" in retirement[0]
    assert "first retire its dataset bind" in retirement[-2]
    assert "Unknown exposure state is a hold" in retirement[-2]
    assert "remove only that backing tree" in retirement[-1]
    assert "Never remove role accounts" in retirement[-1]


@pytest.mark.parametrize(
    "case",
    [
        "tcp",
        "datagram",
        "not-listening",
        "abstract",
        "relative",
        "untrusted-path",
        "root-operator",
        "shared-operator",
        "self-operator",
        "bool-operator",
        "negative-operator",
        "noninteger-operator",
    ],
)
def test_actual_namespace_identity_refuses_invalid_authority_listener(
    tmp_path: Path, case: str
) -> None:
    """Observe native UID1001 validation refusals without creating any host account."""
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
        return
    assert os.geteuid() != 0
    source = Path(__file__).resolve().parents[1]
    script = """import json,os,socket,sys
from pathlib import Path
source,site,directory,case,coverage_dir=sys.argv[1:]
sys.path[:0]=[source+'/src',source,site]
recorder=None
if coverage_dir:
 import coverage
 recorder=coverage.Coverage(
  data_file=str(Path(coverage_dir)/'.coverage'),data_suffix=True,branch=True,
  source_pkgs=['synapse_channel.core.protected_write_operator_socket'],config_file=False)
 recorder.start()
from synapse_channel.core.protected_write_operator_socket import (
 validate_protected_authority_listener)
assert os.getresuid()==(1001,)*3 and os.getresgid()==(1001,)*3
os.chdir(directory)
root=os.open('.',os.O_RDONLY|os.O_DIRECTORY)
operators={'root-operator':0,'shared-operator':1000,'self-operator':1001,
 'bool-operator':True,'negative-operator':-1,'noninteger-operator':1.5}
family=socket.AF_INET if case=='tcp' else socket.AF_UNIX
kind=socket.SOCK_DGRAM if case=='datagram' else socket.SOCK_STREAM
try:
 with socket.socket(family,kind)as listener:
  if case in {'not-listening','relative','untrusted-path','abstract'}:
   name=(f'/proc/self/fd/{root}/listener.sock'if case=='untrusted-path'
    else '\\0synapse-'+str(os.getpid())if case=='abstract'else 'listener.sock')
   listener.bind(name)
   if case!='not-listening':listener.listen(1)
  try:validate_protected_authority_listener(listener,operators.get(case,1002),listening=True)
  except ValueError as error:
   print(json.dumps({'case':case,'uid':os.geteuid(),'refusal':str(error),
    'host_qualification':False}))
  else:raise AssertionError('invalid authority listener accepted')
finally:
 os.close(root)
 if recorder is not None:recorder.stop();recorder.save()
"""
    coverage_dir = os.environ.get("SYNAPSE_TEST_COVERAGE_DIR", "")
    command = [
        "/usr/bin/bwrap",
        "--unshare-user",
        "--uid",
        "1001",
        "--gid",
        "1001",
        "--unshare-net",
        "--new-session",
        "--ro-bind",
        "/",
        "/",
        "--proc",
        "/proc",
        "--bind",
        str(tmp_path),
        str(tmp_path),
    ]
    if coverage_dir:
        assert Path(coverage_dir).is_absolute() and Path(coverage_dir).is_dir()
        command.extend(("--bind", coverage_dir, coverage_dir))
    command.extend(
        (
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-c",
            script,
            str(source),
            str(Path(pytest.__file__).parents[1]),
            str(tmp_path),
            case,
            coverage_dir,
        )
    )
    child = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout.splitlines()[-1])
    expected = (
        "operator listener must be a Unix stream socket"
        if case in {"tcp", "datagram"}
        else "operator listener must be listening under the authority UID"
        if case == "not-listening"
        else "operator listener requires an absolute filesystem path"
        if case in {"abstract", "relative"}
        else "operator listener must be root-owned"
        if case == "untrusted-path"
        else "operator and authority require separate dedicated UIDs"
    )
    assert result == {"case": case, "uid": 1001, "refusal": expected, "host_qualification": False}


@pytest.mark.parametrize("case", ["echo", "changed-response", "wrong-peer"])
async def test_actual_prerequisite_peer_exchange_checks_frame_and_kernel_uid(
    tmp_path: Path, case: str
) -> None:
    """Exchange real frames as the current UID without claiming host role separation."""
    from tools.protected_runtime_probe.authority_child import call_endpoint
    from tools.protected_runtime_probe.native_closure import require_native_runtime_abi
    from websockets.asyncio.server import ServerConnection, unix_serve

    observed = (
        sys.platform,
        platform.machine(),
        sys.version_info[:2],
        sys.maxsize.bit_length() + 1,
    )
    if observed != ("linux", "x86_64", (3, 12), 64):
        with pytest.raises(OSError, match="native Linux"):
            require_native_runtime_abi(*observed)
        return
    endpoint = tmp_path / "endpoint"
    endpoint.mkdir(mode=0o700)
    anchor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    path = Path(f"/proc/self/fd/{anchor}/endpoint/authority.sock")

    async def exchange(connection: ServerConnection) -> None:
        if case == "wrong-peer":
            await connection.wait_closed()
            return
        raw = await connection.recv()
        assert raw == b"synapse-prerequisite-peer-check-v1"
        await connection.send(raw if case == "echo" else b"changed-frame")

    try:
        async with unix_serve(exchange, path=str(path), close_timeout=1, ping_interval=None):
            path.chmod(0o600)
            expected_uid = os.geteuid() + (case == "wrong-peer")
            if case == "echo":
                await asyncio.to_thread(call_endpoint, path, expected_peer_uid=expected_uid)
            else:
                message = "peer UID differs" if case == "wrong-peer" else "echo differs"
                with pytest.raises(ValueError, match=message):
                    await asyncio.to_thread(call_endpoint, path, expected_peer_uid=expected_uid)
    finally:
        path.unlink(missing_ok=True)
        os.close(anchor)


@pytest.mark.parametrize("case", ["authority-help", "authority-refusal", "namespace-refusal"])
def test_actual_native_prerequisite_cli_before_privileged_effects(
    tmp_path: Path, case: str
) -> None:
    """Run the real module entries as the ordinary caller, including argparse help."""
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
        return
    assert os.geteuid() != 0
    source = Path(__file__).resolve().parents[1]
    module = "namespace_child" if case == "namespace-refusal" else "authority_child"
    arguments = ["--help"] if case == "authority-help" else []
    if case == "authority-refusal":
        arguments = ["--cgroup", "/sys/fs/cgroup"]
    child = subprocess.run(
        [sys.executable, "-B", "-m", "tools.protected_runtime_probe." + module, *arguments],
        cwd=source,
        env=dict(
            os.environ,
            PYTHONPATH=str(source / "src") + os.pathsep + str(source),
            PYTHONDONTWRITEBYTECODE="1",
        ),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if case == "authority-help":
        assert child.returncode == 0, child.stderr
        assert "--cgroup" in child.stdout and "usage:" in child.stdout
    else:
        assert child.returncode == 1, child.stderr
        reason = (
            "prerequisite namespace entry requires the root controller"
            if case == "namespace-refusal"
            else "prerequisite resource controls require the root operator"
        )
        assert reason in child.stderr
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize(
    "case",
    [
        "ensure-roles",
        "drop-enrolled",
        "drop-role",
        "resource-create",
        "resource-enter",
        "resource-close",
        "namespace",
        "authority",
        "bootstrap",
    ],
)
async def test_all_public_prerequisite_root_gates_refuse_actual_nonroot(
    tmp_path: Path, case: str
) -> None:
    """Refuse before effects or treating an unqualified resource record as enrolled."""
    from tools.protected_runtime_probe.authority_child import main as authority_main
    from tools.protected_runtime_probe.bootstrap import main as bootstrap_main
    from tools.protected_runtime_probe.namespace_child import main as namespace_main
    from tools.protected_runtime_probe.native_closure import require_native_runtime_abi
    from tools.protected_runtime_probe.resources import (
        ResourceScope,
        create_resource_scope,
        enter_resource_leaf,
    )
    from tools.protected_runtime_probe.roles import (
        Role,
        drop_enrolled_identity,
        drop_privileges,
        ensure_roles,
    )

    observed = (
        sys.platform,
        platform.machine(),
        sys.version_info[:2],
        sys.maxsize.bit_length() + 1,
    )
    if observed != ("linux", "x86_64", (3, 12), 64):
        with pytest.raises(OSError, match="native Linux"):
            require_native_runtime_abi(*observed)
        return
    assert os.getresuid() != (0, 0, 0)
    descriptor: int | None = None
    try:
        with pytest.raises(PermissionError, match="root"):
            if case == "ensure-roles":
                await ensure_roles()
            elif case == "drop-enrolled":
                drop_enrolled_identity(1, 1)
            elif case == "drop-role":
                drop_privileges(Role("synapse-writer", 1, 1))
            elif case == "resource-create":
                create_resource_scope("run-" + "0" * 16)
            elif case == "resource-enter":
                enter_resource_leaf(Path("/sys/fs/cgroup"))
            elif case == "resource-close":
                descriptor = os.open("/sys/fs/cgroup", os.O_RDONLY | os.O_DIRECTORY)
                unqualified = ResourceScope(descriptor, "synapse-qualification-run-" + "0" * 16, ())
                unqualified.close()
            elif case == "namespace":
                namespace_main([])
            elif case == "authority":
                authority_main(["--cgroup", "/sys/fs/cgroup"])
            else:
                bootstrap_main(["--bundle", str(tmp_path), "--manifest-sha256", "0" * 64])
        assert not tuple(tmp_path.iterdir())
        if descriptor is not None:
            assert os.fstat(descriptor).st_ino == Path("/sys/fs/cgroup").stat().st_ino
    finally:
        if descriptor is not None:
            os.close(descriptor)


def test_guest_identity_cannot_change_under_the_original_manifest_approval(tmp_path: Path) -> None:
    """Real payload bytes cannot authorize a different guest under the original digest."""
    digest, _raw = _fixture(tmp_path)
    original = decode_manifest((tmp_path / "manifest.json").read_bytes())
    changed_uuid = original.guest_uuid[:-1] + ("1" if original.guest_uuid[-1] != "1" else "2")
    changed = replace(original, guest_uuid=changed_uuid)
    assert changed.files == original.files
    (tmp_path / "manifest.json").write_bytes(changed.canonical_bytes())
    if os.name != "posix":
        with pytest.raises(OSError, match="POSIX"):
            capture_capsule(tmp_path, digest)
        return
    with pytest.raises(ValueError, match="independent approval"):
        capture_capsule(tmp_path, digest)
    changed_digest = hashlib.sha256(changed.canonical_bytes()).hexdigest()
    captured = capture_capsule(tmp_path, changed_digest)
    assert captured.manifest.guest_uuid == changed_uuid
    assert captured.manifest_sha256 != digest
