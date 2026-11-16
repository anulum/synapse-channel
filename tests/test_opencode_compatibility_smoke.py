# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — verified OpenCode archive installer and real ACP smoke tests
"""Exercise release archive installation and the gated real OpenCode ACP face."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import runpy
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from tools import opencode_compatibility_install as install_module
    from tools.opencode_compatibility_contract import (
        DEFAULT_MANIFEST,
        Artifact,
        load_compatibility,
    )
    from tools.opencode_compatibility_install import (
        SmokeError,
        artifact_url,
        install_archive,
        verify_archive,
    )
    from tools.opencode_compatibility_smoke import (
        _close_pipes,
        _exchange_smoke_initialize,
        _process_environment,
        _validate_initialize,
        smoke_binary,
    )
    from tools.opencode_compatibility_smoke import main as smoke_main
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import opencode_compatibility_install as install_module
    from tools.opencode_compatibility_contract import (
        DEFAULT_MANIFEST,
        Artifact,
        load_compatibility,
    )
    from tools.opencode_compatibility_install import (
        SmokeError,
        artifact_url,
        install_archive,
        verify_archive,
    )
    from tools.opencode_compatibility_smoke import (
        _close_pipes,
        _exchange_smoke_initialize,
        _process_environment,
        _validate_initialize,
        smoke_binary,
    )
    from tools.opencode_compatibility_smoke import main as smoke_main

_PAYLOAD = b"real archive member bytes\n"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(path: Path, *, binary: str = "opencode") -> Artifact:
    return Artifact(
        key="test-platform",
        name=path.name,
        sha256=_digest(path),
        binary=binary,
        runner="",
        smoke=False,
    )


def _tar_archive(path: Path, *, member_type: bytes = tarfile.REGTYPE) -> Path:
    with tarfile.open(path, mode="w:gz") as archive:
        member = tarfile.TarInfo("opencode")
        member.mode = 0o755
        member.size = len(_PAYLOAD)
        member.type = member_type
        if member_type == tarfile.SYMTYPE:
            member.linkname = "../outside"
            member.size = 0
        archive.addfile(member, None if member.size == 0 else io.BytesIO(_PAYLOAD))
    return path


def _zip_archive(path: Path, *, member_type: int = stat.S_IFREG) -> Path:
    member = zipfile.ZipInfo("opencode")
    member.external_attr = (member_type | 0o755) << 16
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            member,
            b"../outside" if member_type == stat.S_IFLNK else _PAYLOAD,
        )
    return path


@pytest.mark.parametrize("archive_kind", ["tar", "zip"])
def test_installer_extracts_only_the_verified_root_binary(
    tmp_path: Path,
    archive_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = (
        _tar_archive(tmp_path / "opencode-test.tar.gz")
        if archive_kind == "tar"
        else _zip_archive(tmp_path / "opencode-test.zip")
    )
    artifact = _artifact(archive)
    destination = tmp_path / "bin" / "opencode"

    install_archive(archive, artifact, destination)

    assert destination.read_bytes() == _PAYLOAD
    assert destination.stat().st_mode & stat.S_IXUSR
    with pytest.raises(SmokeError, match="already exists"):
        install_archive(archive, artifact, destination)

    if archive_kind == "zip":
        real_open = os.open

        def reject_darwin_incompatible_flags(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if flags & os.O_CREAT and flags & getattr(os, "O_NOFOLLOW", 0):
                raise OSError("Darwin rejects O_CREAT combined with O_NOFOLLOW")
            if dir_fd is None:
                return real_open(path, flags, mode)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", reject_darwin_incompatible_flags)
        portable_destination = tmp_path / "portable" / "opencode"
        install_archive(archive, artifact, portable_destination)
        assert portable_destination.read_bytes() == _PAYLOAD


@pytest.mark.parametrize("archive_kind", ["tar", "zip"])
def test_installer_refuses_link_members(tmp_path: Path, archive_kind: str) -> None:
    archive = (
        _tar_archive(tmp_path / "opencode-link.tar.gz", member_type=tarfile.SYMTYPE)
        if archive_kind == "tar"
        else _zip_archive(tmp_path / "opencode-link.zip", member_type=stat.S_IFLNK)
    )
    destination = tmp_path / "opencode"

    with pytest.raises(SmokeError, match="regular file"):
        install_archive(archive, _artifact(archive), destination)

    assert not destination.exists()


def test_installer_refuses_fifo_marked_zip_member(tmp_path: Path) -> None:
    archive = _zip_archive(
        tmp_path / "opencode-fifo.zip",
        member_type=stat.S_IFIFO,
    )
    destination = tmp_path / "opencode"

    with pytest.raises(SmokeError, match="regular file"):
        install_archive(archive, _artifact(archive), destination)

    assert not destination.exists()


def test_installer_refuses_symlinked_destination_ancestors(tmp_path: Path) -> None:
    archive = _tar_archive(tmp_path / "opencode-test.tar.gz")
    requested = tmp_path / "requested"
    outside = tmp_path / "outside"
    requested.mkdir()
    outside.mkdir()
    link = requested / "link"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SmokeError, match="exclusive OpenCode output"):
        install_archive(archive, _artifact(archive), link / "opencode")

    assert not (outside / "opencode").exists()


def test_archive_verification_refuses_digest_drift(tmp_path: Path) -> None:
    archive = _tar_archive(tmp_path / "opencode-test.tar.gz")
    artifact = _artifact(archive)
    changed = Artifact(
        key=artifact.key,
        name=artifact.name,
        sha256="0" * 64,
        binary=artifact.binary,
        runner=artifact.runner,
        smoke=artifact.smoke,
    )

    with pytest.raises(SmokeError, match="digest mismatch"):
        verify_archive(archive, changed)


@pytest.mark.parametrize("contents", [b"not a zip archive", b""])
def test_installer_removes_partial_destination_for_malformed_archives(
    tmp_path: Path, contents: bytes
) -> None:
    archive = tmp_path / "opencode-test.zip"
    archive.write_bytes(contents)
    destination = tmp_path / "opencode"

    with pytest.raises(SmokeError, match="no valid end|non-empty bounded"):
        install_archive(archive, _artifact(archive), destination)

    assert not destination.exists()


@pytest.mark.parametrize(
    ("member", "payload", "message"),
    [
        ("nested/opencode", _PAYLOAD, "archive root"),
        ("opencode", b"", "empty binary"),
    ],
)
def test_installer_refuses_missing_or_empty_root_binary(
    tmp_path: Path, member: str, payload: bytes, message: str
) -> None:
    archive = tmp_path / "opencode-test.zip"
    with zipfile.ZipFile(archive, mode="w") as bundle:
        bundle.writestr(member, payload)
    destination = tmp_path / "opencode"

    with pytest.raises(SmokeError, match=message):
        install_archive(archive, _artifact(archive), destination)

    assert not destination.exists()


@pytest.mark.parametrize("archive_kind", ["tar", "zip"])
def test_installer_refuses_archive_member_fanout(tmp_path: Path, archive_kind: str) -> None:
    if archive_kind == "tar":
        archive = tmp_path / "opencode-test.tar.gz"
        with tarfile.open(archive, mode="w:gz") as bundle:
            for index in range(17):
                name = "opencode" if index == 0 else f"extra-{index}"
                member = tarfile.TarInfo(name)
                member.size = len(_PAYLOAD)
                bundle.addfile(member, io.BytesIO(_PAYLOAD))
    else:
        archive = tmp_path / "opencode-test.zip"
        with zipfile.ZipFile(archive, mode="w") as bundle:
            bundle.writestr("opencode", _PAYLOAD)
            for index in range(1, 17):
                bundle.writestr(f"extra-{index}", _PAYLOAD)

    with pytest.raises(SmokeError, match="metadata limit|only the exact root"):
        install_archive(archive, _artifact(archive), tmp_path / "installed")


def test_installer_refuses_excessive_tar_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _tar_archive(tmp_path / "opencode-test.tar.gz")
    monkeypatch.setattr(install_module, "_MAX_TAR_STREAM_BYTES", 32)

    with pytest.raises(SmokeError, match="expanded-stream limit"):
        install_archive(archive, _artifact(archive), tmp_path / "installed")

    assert not (tmp_path / "installed").exists()


def test_offline_install_cli_uses_the_same_manifest_and_archive_contract(tmp_path: Path) -> None:
    archive = _tar_archive(tmp_path / "opencode-linux-x64.tar.gz")
    data = cast(dict[str, Any], json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8")))
    artifacts = cast(dict[str, dict[str, Any]], data["artifacts"])
    artifacts["linux-x64"]["sha256"] = _digest(archive)
    manifest = tmp_path / "compatibility.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    destination = tmp_path / "installed" / "opencode"
    report = tmp_path / "install-report.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.opencode_compatibility_smoke",
            "--manifest",
            str(manifest),
            "install",
            "--platform",
            "linux-x64",
            "--archive",
            str(archive),
            "--destination",
            str(destination),
            "--report",
            str(report),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert destination.read_bytes() == _PAYLOAD
    assert json.loads(completed.stdout)["archive_sha256"] == _digest(archive)
    assert json.loads(report.read_text(encoding="utf-8"))["platform"] == "linux-x64"


def test_official_artifact_url_is_immutable() -> None:
    contract = load_compatibility()
    artifact = contract.artifact("windows-x64")

    assert artifact_url(contract, artifact) == (
        "https://github.com/anomalyco/opencode/releases/download/v1.18.32/opencode-windows-x64.zip"
    )

    with pytest.raises(SmokeError, match="official repository"):
        artifact_url(replace(contract, repository="example.invalid/opencode"), artifact)


def test_smoke_refuses_non_regular_or_wrong_version_executables(tmp_path: Path) -> None:
    contract = load_compatibility()
    with pytest.raises(SmokeError, match="regular file"):
        smoke_binary(tmp_path, contract)
    with pytest.raises(SmokeError, match="version mismatch"):
        smoke_binary(Path(sys.executable).resolve(), contract)


def test_smoke_environment_excludes_ambient_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SYNAPSE_AUDIT_SECRET", "ambient-sentinel")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-cloud-secret")

    environment = _process_environment(tmp_path)

    assert "SYNAPSE_AUDIT_SECRET" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert environment["HOME"] == str(tmp_path)
    assert environment["OPENCODE_AUTH_CONTENT"] == "{}"


def _silent_acp_binary(root: Path, version: str, holder_pid: Path) -> Path:
    """A fake OpenCode whose ACP face never answers, with a child holding its stdout."""
    fake = root / "opencode"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import subprocess, sys, time\n"
        "if sys.argv[1:] == ['--version']:\n"
        f"    print({version!r})\n"
        "    raise SystemExit(0)\n"
        "holder = subprocess.Popen(['/bin/sleep', '30'], start_new_session=True)\n"
        f"open({str(holder_pid)!r}, 'w').write(str(holder.pid))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    return fake


def test_a_silent_acp_face_fails_fast_even_while_a_child_holds_its_output(
    tmp_path: Path,
) -> None:
    """The timeout is reported and the cleanup does not wait on the still-held stdout."""
    contract = load_compatibility()
    holder_pid = tmp_path / "holder.pid"
    fake = _silent_acp_binary(tmp_path, contract.version, holder_pid)
    started = time.monotonic()
    try:
        with pytest.raises(SmokeError, match="initialize response timed out"):
            smoke_binary(fake, contract, timeout=1.0)
        assert time.monotonic() - started < 15
    finally:
        if holder_pid.exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(holder_pid.read_text()), signal.SIGKILL)


def test_closing_a_broken_pipe_is_tolerated() -> None:
    """A write that could not reach a closed reader must not turn cleanup into an error."""
    read_end, write_end = os.pipe()
    os.close(read_end)
    pipe = os.fdopen(write_end, "w")
    pipe.write("unsent")
    _close_pipes(pipe)
    assert pipe.closed


def _valid_initialize_response(version: str) -> dict[str, Any]:
    """An initialize answer that meets every requirement of the pinned contract."""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": 1,
            "agentInfo": {"name": "OpenCode", "version": version},
            "agentCapabilities": {"mcpCapabilities": {"http": True, "sse": True}},
            "authMethods": [
                {"_meta": "not an object"},
                "not a method",
                {"_meta": {"terminal-auth": "not an object"}},
                {
                    "_meta": {
                        "terminal-auth": {
                            "command": "opencode",
                            "args": ["auth", "login"],
                            "label": "Log in",
                        }
                    }
                },
            ],
        },
    }


def _scripted_opencode(root: Path, version: str, after_request: str) -> Path:
    """A fake OpenCode that answers ``--version`` and runs ``after_request`` on its ACP face.

    The ACP face first reads the initialize request line, so the smoke's write always lands.
    """
    fake = root / "opencode"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, sys, time\n"
        "if sys.argv[1:] == ['--version']:\n"
        f"    print({version!r})\n"
        "    raise SystemExit(0)\n"
        "sys.stdin.readline()\n" + after_request,
        encoding="utf-8",
    )
    fake.chmod(0o700)
    return fake


def test_smoke_reports_a_valid_initialize_exchange(tmp_path: Path) -> None:
    """A conforming ACP face that exits after stdin closes yields the capability report."""
    contract = load_compatibility()
    answer = json.dumps(_valid_initialize_response(contract.version))
    fake = _scripted_opencode(
        tmp_path, contract.version, f"print({answer!r}, flush=True)\nsys.stdin.read()\n"
    )
    assert smoke_binary(fake, contract, timeout=10.0) == {
        "agent": "OpenCode",
        "mcp_http": True,
        "mcp_sse": True,
        "protocol_version": 1,
        "terminal_auth": True,
        "version": contract.version,
    }


@pytest.mark.parametrize(
    ("after_request", "message"),
    [
        ("raise SystemExit(0)\n", "closed stdout before initialize response"),
        ("print('not json', flush=True)\nsys.stdin.read()\n", "returned malformed JSON"),
    ],
)
def test_smoke_refuses_an_acp_face_without_a_json_answer(
    tmp_path: Path, after_request: str, message: str
) -> None:
    """No answer and an answer that is not JSON are both refusals, not a report."""
    contract = load_compatibility()
    fake = _scripted_opencode(tmp_path, contract.version, after_request)
    with pytest.raises(SmokeError, match=message):
        smoke_binary(fake, contract, timeout=10.0)


def test_smoke_names_the_exit_status_and_stderr_of_a_failing_acp_face(tmp_path: Path) -> None:
    """A valid answer followed by a non-zero exit is still a failure, with its stderr."""
    contract = load_compatibility()
    answer = json.dumps(_valid_initialize_response(contract.version))
    fake = _scripted_opencode(
        tmp_path,
        contract.version,
        f"print({answer!r}, flush=True)\nsys.stdin.read()\n"
        "sys.stderr.write('acp face broke')\nraise SystemExit(3)\n",
    )
    with pytest.raises(SmokeError, match=r"exited 3: acp face broke"):
        smoke_binary(fake, contract, timeout=10.0)


def test_smoke_refuses_an_acp_face_that_ignores_stdin_eof(tmp_path: Path) -> None:
    """A face that answers but ignores stdin EOF and SIGTERM fails, and the cleanup kills it."""
    contract = load_compatibility()
    answer = json.dumps(_valid_initialize_response(contract.version))
    fake = _scripted_opencode(
        tmp_path,
        contract.version,
        "import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"print({answer!r}, flush=True)\ntime.sleep(60)\n",
    )
    with pytest.raises(SmokeError, match="did not exit after stdin EOF"):
        smoke_binary(fake, contract, timeout=10.0)


def test_smoke_refuses_a_missing_binary_and_a_failing_version_probe(tmp_path: Path) -> None:
    """A path that cannot be inspected, a file that cannot run and a probe that hangs."""
    contract = load_compatibility()
    with pytest.raises(SmokeError, match="cannot inspect OpenCode binary"):
        smoke_binary(tmp_path / "absent", contract)
    not_executable = tmp_path / "opencode-not-executable"
    not_executable.write_text("#!/bin/sh\necho 0\n", encoding="utf-8")
    not_executable.chmod(0o600)
    with pytest.raises(SmokeError, match="version probe failed"):
        smoke_binary(not_executable, contract)
    hanging = tmp_path / "opencode-hanging"
    hanging.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n", encoding="utf-8")
    hanging.chmod(0o700)
    with pytest.raises(SmokeError, match="version probe failed"):
        smoke_binary(hanging, contract, timeout=0.5)


class _ExitedProcess:
    """The part of ``subprocess.Popen`` the exchange reads, for a process that already exited."""

    def __init__(self, stdin: Any, stdout: Any, stderr: Any) -> None:
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = 0

    def poll(self) -> int:
        return 0


class _BrokenStdin:
    """A stdin whose write fails the way a broken pipe does."""

    closed = False

    def write(self, _text: str) -> int:
        raise OSError("broken pipe")

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def test_the_exchange_refuses_a_process_that_cannot_start_or_has_no_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A start failure and a process without pipes are refusals before any write."""
    contract = load_compatibility()

    def cannot_start(*_args: object, **_kwargs: object) -> Any:
        raise OSError("exec format error")

    monkeypatch.setattr(subprocess, "Popen", cannot_start)
    with pytest.raises(SmokeError, match="process could not start"):
        _exchange_smoke_initialize(
            tmp_path / "opencode", contract, workspace=tmp_path, environment={}, timeout=1.0
        )

    def without_pipes(*_args: object, **_kwargs: object) -> Any:
        return _ExitedProcess(None, None, None)

    monkeypatch.setattr(subprocess, "Popen", without_pipes)
    with pytest.raises(SmokeError, match="pipes were not created"):
        _exchange_smoke_initialize(
            tmp_path / "opencode", contract, workspace=tmp_path, environment={}, timeout=1.0
        )


def test_the_exchange_reports_a_pipe_failure_and_closes_its_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write that fails is a pipe I/O refusal, and the cleanup still closes every pipe."""
    contract = load_compatibility()
    stdout_read, stdout_write = os.pipe()
    stderr_read, stderr_write = os.pipe()
    os.close(stdout_write)
    os.close(stderr_write)
    stdout = os.fdopen(stdout_read, "r")
    stderr = os.fdopen(stderr_read, "r")
    stdin = _BrokenStdin()
    process = _ExitedProcess(stdin, stdout, stderr)

    def started(*_args: object, **_kwargs: object) -> Any:
        return process

    monkeypatch.setattr(subprocess, "Popen", started)
    with pytest.raises(SmokeError, match="pipe I/O failed"):
        _exchange_smoke_initialize(
            tmp_path / "opencode", contract, workspace=tmp_path, environment={}, timeout=1.0
        )
    assert stdin.closed and stderr.closed


def _mutated(version: str, change: str) -> object:
    """One valid initialize answer with exactly one requirement broken."""
    response = _valid_initialize_response(version)
    result = response["result"]
    if change == "not a dict":
        return ["not", "an", "object"]
    if change == "jsonrpc":
        response["jsonrpc"] = "1.0"
    elif change == "id":
        response["id"] = 2
    elif change == "error":
        response["error"] = {"code": -1}
    elif change == "result":
        response["result"] = "not an object"
    elif change == "protocol":
        result["protocolVersion"] = 2
    elif change == "agent name":
        result["agentInfo"] = {"name": "Other", "version": version}
    elif change == "agent version":
        result["agentInfo"] = {"name": "OpenCode", "version": "0.0.0"}
    elif change == "mcp":
        result["agentCapabilities"] = {"mcpCapabilities": {"http": True, "sse": False}}
    elif change == "no auth methods":
        result["authMethods"] = "not a list"
    elif change == "auth missing":
        result["authMethods"] = [{"_meta": {}}]
    elif change == "auth command":
        result["authMethods"] = [
            {"_meta": {"terminal-auth": {"command": "x", "args": ["auth", "login"], "label": "L"}}}
        ]
    elif change == "auth args":
        result["authMethods"] = [
            {"_meta": {"terminal-auth": {"command": "opencode", "args": ["login"], "label": "L"}}}
        ]
    elif change == "auth label":
        result["authMethods"] = [
            {"_meta": {"terminal-auth": {"command": "opencode", "args": ["auth", "login"]}}}
        ]
    return response


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("not a dict", "invalid JSON-RPC envelope"),
        ("jsonrpc", "invalid JSON-RPC envelope"),
        ("id", "failed correlation"),
        ("error", "failed correlation"),
        ("result", "protocol version 1"),
        ("protocol", "protocol version 1"),
        ("agent name", "unexpected agent identity"),
        ("agent version", "differs from the pinned CLI"),
        ("mcp", "MCP capabilities changed"),
        ("no auth methods", "terminal authentication capability is absent"),
        ("auth missing", "terminal authentication capability is absent"),
        ("auth command", "terminal authentication capability is absent"),
        ("auth args", "terminal authentication capability is absent"),
        ("auth label", "terminal authentication capability is absent"),
    ],
)
def test_initialize_validation_refuses_each_broken_requirement(change: str, message: str) -> None:
    """Every requirement of the pinned ACP face is checked, one broken field at a time."""
    contract = load_compatibility()
    with pytest.raises(SmokeError, match=message):
        _validate_initialize(_mutated(contract.version, change), contract)


@pytest.mark.parametrize("given_archive", [False, True])
def test_main_installs_a_given_or_downloaded_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    given_archive: bool,
) -> None:
    """The CLI installs ``--archive`` or a download, and prints the report it writes."""
    archive = _tar_archive(tmp_path / "opencode-linux-x64.tar.gz")
    data = cast(dict[str, Any], json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8")))
    artifacts = cast(dict[str, dict[str, Any]], data["artifacts"])
    artifacts["linux-x64"]["sha256"] = _digest(archive)
    manifest = tmp_path / "compatibility.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    destination = tmp_path / "installed" / "opencode"
    report = tmp_path / "install-report.json"
    downloads: list[Path] = []

    def download(_contract: object, _artifact: object, target: Path) -> None:
        downloads.append(target)
        shutil.copyfile(archive, target)

    monkeypatch.setattr("tools.opencode_compatibility_smoke.download_archive", download)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "opencode_compatibility_smoke",
            "--manifest",
            str(manifest),
            "install",
            "--platform",
            "linux-x64",
            "--destination",
            str(destination),
            "--report",
            str(report),
            *(["--archive", str(archive)] if given_archive else []),
        ],
    )
    assert smoke_main() == 0
    assert len(downloads) == (0 if given_archive else 1)
    assert all(target.name == archive.name for target in downloads)
    assert destination.read_bytes() == _PAYLOAD
    printed = json.loads(capsys.readouterr().out)
    assert printed == json.loads(report.read_text(encoding="utf-8"))
    assert printed["archive_sha256"] == _digest(archive)


def test_main_smoke_writes_and_prints_the_capability_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ``smoke`` command prints the report it writes."""
    contract = load_compatibility()
    answer = json.dumps(_valid_initialize_response(contract.version))
    fake = _scripted_opencode(
        tmp_path, contract.version, f"print({answer!r}, flush=True)\nsys.stdin.read()\n"
    )
    report = tmp_path / "smoke-report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "opencode_compatibility_smoke",
            "smoke",
            "--binary",
            str(fake),
            "--timeout",
            "10",
            "--report",
            str(report),
        ],
    )
    assert smoke_main() == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == json.loads(report.read_text(encoding="utf-8"))
    assert printed["terminal_auth"] is True


def test_running_the_tool_turns_a_refusal_into_its_exit_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run as a script, a refusal exits with its message instead of a traceback."""
    script = Path(__file__).resolve().parents[1] / "tools" / "opencode_compatibility_smoke.py"
    monkeypatch.setattr(sys, "argv", [str(script), "smoke", "--binary", str(tmp_path / "absent")])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(script), run_name="__main__")
    assert "cannot inspect OpenCode binary" in str(stopped.value.code)


def test_real_pinned_opencode_negotiates_the_expected_acp_face() -> None:
    configured = os.environ.get("OPENCODE_BIN", "").strip()
    discovered = configured or shutil.which("opencode") or ""
    if not discovered:
        pytest.skip("real OpenCode compatibility smoke requires OPENCODE_BIN")
    binary = Path(discovered).resolve()
    version = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    contract = load_compatibility()
    if version.returncode != 0 or version.stdout.strip() != contract.version:
        pytest.skip(f"real OpenCode compatibility smoke requires {contract.version}")

    relative_binary = Path(os.path.relpath(binary, Path.cwd()))
    assert smoke_binary(relative_binary, contract) == {
        "agent": "OpenCode",
        "mcp_http": True,
        "mcp_sse": True,
        "protocol_version": 1,
        "terminal_auth": True,
        "version": "1.18.32",
    }
