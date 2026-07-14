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
import json
import os
import queue
import stat
import subprocess  # nosec B404
import tempfile
import threading
from pathlib import Path

from tools.opencode_compatibility_contract import (
    DEFAULT_MANIFEST,
    Compatibility,
    CompatibilityError,
    load_compatibility,
)
from tools.opencode_compatibility_install import (
    SmokeError,
    download_archive,
    install_archive,
    write_report,
)

_SAFE_INHERITED_ENV = (
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
)


def _process_environment(home: Path) -> dict[str, str]:
    environment = {
        key: os.environ[key] for key in _SAFE_INHERITED_ENV if key in os.environ and os.environ[key]
    }
    temporary = home / "tmp"
    environment.update(
        {
            "APPDATA": str(home / ".config"),
            "HOME": str(home),
            "LOCALAPPDATA": str(home / ".local" / "share"),
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "TMPDIR": str(temporary),
            "USER": "synapse-opencode",
            "USERNAME": "synapse-opencode",
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
    binary = Path(os.path.abspath(binary))
    try:
        status = binary.lstat()
    except OSError as exc:
        raise SmokeError(f"cannot inspect OpenCode binary: {binary}") from exc
    if not stat.S_ISREG(status.st_mode):
        raise SmokeError("OpenCode smoke target must be a regular file")

    with tempfile.TemporaryDirectory(prefix="synapse-opencode-smoke-") as temporary:
        root = Path(temporary)
        home = root / "home"
        workspace = root / "workspace"
        home.mkdir(mode=0o700)
        workspace.mkdir(mode=0o700)
        (home / "tmp").mkdir(mode=0o700)
        environment = _process_environment(home)
        try:
            completed = subprocess.run(  # nosec B603
                [str(binary), "--version"],
                cwd=workspace,
                env=environment,
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
        try:
            process = subprocess.Popen(  # nosec B603
                [str(binary), "acp", "--cwd", str(workspace)],
                cwd=workspace,
                env=environment,
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
                # macOS exposes its private temp tree through /var -> /private/var.
                # Resolve only this freshly created, process-owned root before
                # appending the archive name; caller destinations stay fail-closed.
                archive = Path(temporary).resolve(strict=True) / artifact.name
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
        write_report(args.report, report)
        print(json.dumps(report, sort_keys=True))
        return 0
    report = smoke_binary(args.binary, contract, timeout=args.timeout)
    write_report(args.report, report)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CompatibilityError, SmokeError) as error:
        raise SystemExit(str(error)) from error
