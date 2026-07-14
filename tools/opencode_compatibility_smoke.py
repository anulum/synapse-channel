# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — verified OpenCode release installation and ACP smoke
"""Install one pinned OpenCode artifact and exercise its real ACP process."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import stat
import subprocess  # nosec B404
import tarfile
import tempfile
import threading
import zipfile
from collections.abc import Mapping
from http.client import HTTPMessage
from pathlib import Path
from typing import IO
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from tools.opencode_compatibility_contract import (
    DEFAULT_MANIFEST,
    Artifact,
    Compatibility,
    CompatibilityError,
    load_compatibility,
)

_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_MAX_BINARY_BYTES = 256 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_ALLOWED_DOWNLOAD_HOSTS = frozenset({"github.com", "objects.githubusercontent.com"})


class SmokeError(RuntimeError):
    """The pinned artifact could not be installed or its ACP face failed."""


class _PinnedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        _require_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _require_download_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or (
        host not in _ALLOWED_DOWNLOAD_HOSTS and not host.endswith(".githubusercontent.com")
    ):
        raise SmokeError(f"OpenCode download redirected outside approved HTTPS hosts: {url}")


def artifact_url(contract: Compatibility, artifact: Artifact) -> str:
    """Return the immutable official release URL for one manifest artifact."""
    if contract.repository != "anomalyco/opencode":
        raise SmokeError("OpenCode artifact URL requires the official repository")
    url = (
        f"https://github.com/{contract.repository}/releases/download/{contract.tag}/{artifact.name}"
    )
    _require_download_url(url)
    return url


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(_CHUNK_BYTES), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SmokeError(f"cannot read OpenCode archive: {path}") from exc
    return digest.hexdigest()


def verify_archive(path: Path, artifact: Artifact) -> None:
    """Require a regular bounded archive with the manifest SHA-256 digest."""
    try:
        status = path.lstat()
    except OSError as exc:
        raise SmokeError(f"cannot inspect OpenCode archive: {path}") from exc
    if not stat.S_ISREG(status.st_mode) or status.st_size > _MAX_ARCHIVE_BYTES:
        raise SmokeError("OpenCode archive must be a bounded regular file")
    actual = _sha256(path)
    if actual != artifact.sha256:
        raise SmokeError(
            f"OpenCode archive digest mismatch for {artifact.name}: "
            f"expected {artifact.sha256}, got {actual}"
        )


def download_archive(contract: Compatibility, artifact: Artifact, destination: Path) -> None:
    """Download one exact release asset over HTTPS and verify it before use."""
    if destination.exists() or destination.is_symlink():
        raise SmokeError(f"download destination already exists: {destination}")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    request = Request(
        artifact_url(contract, artifact),
        headers={"Accept": "application/octet-stream", "User-Agent": "synapse-channel"},
    )
    opener = build_opener(_PinnedRedirectHandler())
    written = 0
    digest = hashlib.sha256()
    try:
        with opener.open(request, timeout=90) as response:  # nosec B310
            _require_download_url(response.geturl())
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > _MAX_ARCHIVE_BYTES:
                raise SmokeError("OpenCode release archive exceeds the download limit")
            with destination.open("xb") as output:
                os.chmod(destination, 0o600)
                while chunk := response.read(_CHUNK_BYTES):
                    written += len(chunk)
                    if written > _MAX_ARCHIVE_BYTES:
                        raise SmokeError("OpenCode release archive exceeds the download limit")
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
    except (HTTPError, URLError, OSError, ValueError, SmokeError) as exc:
        destination.unlink(missing_ok=True)
        if isinstance(exc, SmokeError):
            raise
        raise SmokeError(f"cannot download {artifact.name}: {exc}") from exc
    if written == 0 or digest.hexdigest() != artifact.sha256:
        destination.unlink(missing_ok=True)
        raise SmokeError(
            f"downloaded OpenCode archive failed integrity verification: {artifact.name}"
        )


def _copy_bounded(source: IO[bytes], destination: IO[bytes]) -> int:
    written = 0
    while chunk := source.read(_CHUNK_BYTES):
        written += len(chunk)
        if written > _MAX_BINARY_BYTES:
            raise SmokeError("OpenCode binary exceeds the extraction limit")
        destination.write(chunk)
    if written == 0:
        raise SmokeError("OpenCode archive contains an empty binary")
    return written


def _zip_member(archive: zipfile.ZipFile, name: str) -> zipfile.ZipInfo:
    matches = [item for item in archive.infolist() if item.filename == name]
    if len(matches) != 1:
        raise SmokeError(f"OpenCode ZIP must contain exactly one root {name!r} member")
    member = matches[0]
    mode = member.external_attr >> 16
    if member.is_dir() or stat.S_ISLNK(mode) or member.file_size > _MAX_BINARY_BYTES:
        raise SmokeError("OpenCode ZIP binary member is not a bounded regular file")
    return member


def _tar_member(archive: tarfile.TarFile, name: str) -> tarfile.TarInfo:
    matches = [item for item in archive.getmembers() if item.name == name]
    if len(matches) != 1:
        raise SmokeError(f"OpenCode tarball must contain exactly one root {name!r} member")
    member = matches[0]
    if not member.isfile() or member.size > _MAX_BINARY_BYTES:
        raise SmokeError("OpenCode tarball binary member is not a bounded regular file")
    return member


def _copy_archive_member(archive_path: Path, artifact: Artifact, destination: IO[bytes]) -> int:
    try:
        if artifact.name.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as archive:
                zip_member = _zip_member(archive, artifact.binary)
                with archive.open(zip_member, "r") as source:
                    return _copy_bounded(source, destination)
        if artifact.name.endswith(".tar.gz"):
            with tarfile.open(archive_path, mode="r:gz") as archive:
                tar_member = _tar_member(archive, artifact.binary)
                tar_source = archive.extractfile(tar_member)
                if tar_source is None:
                    raise SmokeError("OpenCode tarball binary member could not be opened")
                with tar_source:
                    return _copy_bounded(tar_source, destination)
    except SmokeError:
        raise
    except (
        EOFError,
        KeyError,
        OSError,
        RuntimeError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as exc:
        raise SmokeError(f"cannot inspect OpenCode archive: {archive_path}") from exc
    raise SmokeError(f"unsupported OpenCode archive format: {artifact.name}")


def install_archive(archive_path: Path, artifact: Artifact, destination: Path) -> None:
    """Verify and extract only the exact root binary without overwriting a path."""
    verify_archive(archive_path, artifact)
    if destination.exists() or destination.is_symlink():
        raise SmokeError(f"binary destination already exists: {destination}")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(destination, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            descriptor = -1
            _copy_archive_member(archive_path, artifact, output)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(destination, 0o700)
    except (OSError, SmokeError) as exc:
        if descriptor >= 0:
            os.close(descriptor)
        destination.unlink(missing_ok=True)
        if isinstance(exc, SmokeError):
            raise
        raise SmokeError(f"cannot install OpenCode binary: {destination}") from exc


def _process_environment(home: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
            "OPENCODE_AUTH_CONTENT": "{}",
            "OPENCODE_CONFIG_CONTENT": "{}",
            "OPENCODE_DISABLE_AUTOCOMPACT": "1",
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "OPENCODE_DISABLE_MODELS_FETCH": "1",
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "OPENCODE_PURE": "1",
            "OPENCODE_TEST_HOME": str(home),
            "NO_COLOR": "1",
        }
    )
    return environment


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _validate_initialize(response: object, contract: Compatibility) -> dict[str, object]:
    if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
        raise SmokeError("OpenCode ACP returned an invalid JSON-RPC envelope")
    if response.get("id") != 1 or "error" in response:
        raise SmokeError("OpenCode ACP initialize request failed correlation")
    result = response.get("result")
    if not isinstance(result, dict) or result.get("protocolVersion") != 1:
        raise SmokeError("OpenCode ACP did not negotiate protocol version 1")
    agent = result.get("agentInfo")
    if not isinstance(agent, dict) or agent.get("name") != "OpenCode":
        raise SmokeError("OpenCode ACP returned unexpected agent identity")
    if agent.get("version") != contract.version:
        raise SmokeError("OpenCode ACP agent version differs from the pinned CLI")
    capabilities = result.get("agentCapabilities")
    if not isinstance(capabilities, dict) or capabilities.get("mcpCapabilities") != {
        "http": True,
        "sse": True,
    }:
        raise SmokeError("OpenCode ACP MCP capabilities changed")
    methods = result.get("authMethods")
    terminal_auth = None
    if isinstance(methods, list):
        for method in methods:
            if not isinstance(method, dict) or not isinstance(method.get("_meta"), dict):
                continue
            candidate = method["_meta"].get("terminal-auth")
            if isinstance(candidate, dict):
                terminal_auth = candidate
                break
    if terminal_auth is None or (
        terminal_auth.get("command") != "opencode"
        or terminal_auth.get("args") != ["auth", "login"]
        or not isinstance(terminal_auth.get("label"), str)
    ):
        raise SmokeError("OpenCode ACP terminal authentication capability is absent")
    return {
        "agent": "OpenCode",
        "mcp_http": True,
        "mcp_sse": True,
        "protocol_version": 1,
        "terminal_auth": True,
        "version": contract.version,
    }


def smoke_binary(
    binary: Path, contract: Compatibility, *, timeout: float = 30.0
) -> dict[str, object]:
    """Require the exact CLI version and one real ACP initialize exchange."""
    try:
        status = binary.lstat()
    except OSError as exc:
        raise SmokeError(f"cannot inspect OpenCode binary: {binary}") from exc
    if not stat.S_ISREG(status.st_mode):
        raise SmokeError("OpenCode smoke target must be a regular file")
    try:
        completed = subprocess.run(  # nosec B603
            [str(binary), "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SmokeError("OpenCode CLI version probe failed") from exc
    if completed.returncode != 0 or completed.stdout.strip() != contract.version:
        raise SmokeError(
            f"OpenCode CLI version mismatch: expected {contract.version}, "
            f"got {completed.stdout.strip() or completed.stderr.strip() or 'no output'}"
        )

    with tempfile.TemporaryDirectory(prefix="synapse-opencode-smoke-") as temporary:
        root = Path(temporary)
        home = root / "home"
        workspace = root / "workspace"
        home.mkdir(mode=0o700)
        workspace.mkdir(mode=0o700)
        try:
            process = subprocess.Popen(  # nosec B603
                [str(binary), "acp", "--cwd", str(workspace)],
                cwd=workspace,
                env=_process_environment(home),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise SmokeError("OpenCode ACP process could not start") from exc
        if process.stdin is None or process.stdout is None or process.stderr is None:
            _terminate(process)
            raise SmokeError("OpenCode ACP pipes were not created")
        stdin = process.stdin
        stdout = process.stdout
        stderr_stream = process.stderr
        responses: queue.Queue[str] = queue.Queue(maxsize=1)

        def read_response() -> None:
            responses.put(stdout.readline())

        reader = threading.Thread(target=read_response, daemon=True)
        reader.start()
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "clientCapabilities": {"_meta": {"terminal-auth": True}},
                "clientInfo": {
                    "name": "synapse-channel-compatibility",
                    "version": "1",
                },
            },
        }
        try:
            stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            stdin.flush()
            try:
                line = responses.get(timeout=timeout)
            except queue.Empty as exc:
                raise SmokeError("OpenCode ACP initialize response timed out") from exc
            if not line:
                raise SmokeError("OpenCode ACP closed stdout before initialize response")
            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SmokeError("OpenCode ACP returned malformed JSON") from exc
            stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                raise SmokeError("OpenCode ACP did not exit after stdin EOF") from exc
            stderr = stderr_stream.read()
            if process.returncode != 0:
                raise SmokeError(f"OpenCode ACP exited {process.returncode}: {stderr[-2000:]}")
            return _validate_initialize(response, contract)
        except OSError as exc:
            raise SmokeError("OpenCode ACP pipe I/O failed") from exc
        finally:
            _terminate(process)
            reader.join(timeout=2)


def _write_report(path: Path | None, report: Mapping[str, object]) -> None:
    if path is None:
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as output:
            os.chmod(path, 0o600)
            output.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())
    except OSError as exc:
        raise SmokeError(f"cannot write compatibility report: {path}") from exc


def main() -> int:
    """Install or smoke one manifest-pinned OpenCode release artifact."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    commands = parser.add_subparsers(dest="command", required=True)

    install = commands.add_parser("install")
    install.add_argument("--platform", required=True)
    install.add_argument("--destination", type=Path, required=True)
    install.add_argument("--archive", type=Path)
    install.add_argument("--report", type=Path)

    smoke = commands.add_parser("smoke")
    smoke.add_argument("--binary", type=Path, required=True)
    smoke.add_argument("--timeout", type=float, default=30.0)
    smoke.add_argument("--report", type=Path)

    args = parser.parse_args()
    contract = load_compatibility(args.manifest)
    if args.command == "install":
        artifact = contract.artifact(args.platform)
        if args.archive is None:
            with tempfile.TemporaryDirectory(prefix="synapse-opencode-download-") as temporary:
                archive = Path(temporary) / artifact.name
                download_archive(contract, artifact, archive)
                install_archive(archive, artifact, args.destination)
        else:
            install_archive(args.archive, artifact, args.destination)
        report: dict[str, object] = {
            "archive_sha256": artifact.sha256,
            "binary": str(args.destination),
            "platform": artifact.key,
            "version": contract.version,
        }
        _write_report(args.report, report)
        print(json.dumps(report, sort_keys=True))
        return 0
    report = smoke_binary(args.binary, contract, timeout=args.timeout)
    _write_report(args.report, report)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CompatibilityError, SmokeError) as error:
        raise SystemExit(str(error)) from error
