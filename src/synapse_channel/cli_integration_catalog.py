# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — host integration catalog CLI
"""Inspect reviewed host capabilities and delegate to owned native lifecycles."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import stat
import subprocess  # nosec B404
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import default_hub_uri
from synapse_channel.codex_mcp_package import CodexPackageError, apply_codex_package
from synapse_channel.integration_catalog import HOSTS, HostCapability, host_capability
from synapse_channel.participants.pi_process import PiRpcProcess
from synapse_channel.participants.pi_rpc import PiRpcError


def _binary(args: argparse.Namespace, capability: HostCapability) -> Path | None:
    """Resolve an exact chosen host executable without changing active profiles."""
    name = {
        "claude-code": "claude",
        "codex-cli": "codex",
        "pi": "pi",
        "opencode": "opencode",
        "gemini-cli": "gemini",
    }.get(capability.key)
    if name is None:
        return None
    found = shutil.which(args.host_bin or name)
    return Path(found).absolute() if found is not None else None


def _profile(args: argparse.Namespace, capability: HostCapability) -> Path:
    """Use the host's own default profile convention when no override is given."""
    if args.profile_root:
        return Path(args.profile_root).expanduser().absolute()
    if capability.key == "claude-code":
        return Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    if capability.key == "codex-cli":
        return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    if capability.key == "pi":
        return Path(os.environ.get("PI_CODING_AGENT_DIR", str(Path.home() / ".pi/agent")))
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))


def _host_version(binary: Path | None) -> str | None:
    """Read a bounded non-model host version string or return unavailable."""
    if binary is None:
        return None
    try:
        result = subprocess.run(  # nosec B603
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode or len(result.stdout) > 512:
        return None
    return result.stdout.strip()


def _version_matches(capability: HostCapability, observed: str | None) -> bool:
    """Require the reviewed exact host version for new package admission."""
    return (
        observed is not None
        and capability.verified_version is not None
        and (
            capability.verified_version in observed.split()
            or observed == capability.verified_version
        )
    )


def _child_adapter(args: argparse.Namespace, capability: HostCapability) -> tuple[int, str]:
    """Reuse the installed, separately validated Claude/OpenCode entry points."""
    command = [sys.executable, "-m", "synapse_channel.cli", "adapters"]
    profile = str(_profile(args, capability))
    if capability.key == "claude-code":
        command.extend(["claude-plugin", args.action, "--config-root", profile])
        if args.host_bin:
            command.extend(["--claude-bin", args.host_bin])
    else:
        action = "status" if args.action in {"inspect", "diagnose"} else args.action
        command.extend(["opencode", action, "--scope", "global", "--config-root", profile])
    if args.action == "install":
        command.extend(["--identity", args.identity, "--uri", args.uri])
        if args.synapse_bin:
            command.extend(["--synapse-bin", args.synapse_bin])
        if args.token_file:
            command.extend(["--token-file", args.token_file])
    try:
        result = subprocess.run(  # nosec B603
            command, capture_output=True, text=True, timeout=45, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return 2, "native adapter command failed"
    output = result.stdout.strip() if result.returncode == 0 else result.stderr.strip()
    return result.returncode, output[:2_000]


async def _pi_diagnose(args: argparse.Namespace, binary: Path) -> None:
    """Ask the real offline RPC host whether the exact extension loaded."""
    if not args.pi_model or not args.pi_extension:
        raise ValueError("pi diagnosis requires --pi-model and --pi-extension")
    extension = Path(args.pi_extension).expanduser().resolve(strict=True)
    metadata = extension.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o022:
        raise ValueError("pi extension must be an unmodified private regular file")
    environment = dict(
        os.environ,
        PI_OFFLINE="1",
        PI_TELEMETRY="0",
        PI_CODING_AGENT_DIR=str(_profile(args, host_capability("pi"))),
    )
    with tempfile.TemporaryDirectory(prefix="synapse-pi-diagnose-") as session_dir:
        argv = [
            str(binary),
            "--mode",
            "rpc",
            "--model",
            args.pi_model,
            "--session-id",
            str(uuid.uuid4()),
            "--session-dir",
            session_dir,
            "--no-context-files",
            "--no-extensions",
            "--no-approve",
            "--offline",
            "--no-tools",
            "--extension",
            str(extension),
        ]
        async with PiRpcProcess(argv, cwd=Path.cwd(), environment=environment, timeout=20) as child:
            response = await child.command("get_commands")
            data = response.get("data")
            commands = data.get("commands") if isinstance(data, dict) else None
            if not isinstance(commands, list) or not any(
                isinstance(command, dict)
                and command.get("name") == "synapse-claim-guard-health"
                and command.get("source") == "extension"
                and isinstance(command.get("sourceInfo"), dict)
                and command["sourceInfo"].get("path") == str(extension)
                for command in commands
            ):
                raise ValueError("pi claim guard did not load in the real host")


def _row(capability: HostCapability, observed: str | None) -> dict[str, Any]:
    """Return a stable secret-free review row."""
    return {
        "host": capability.key,
        "name": capability.name,
        "capability": capability.capability,
        "package_kind": capability.package_kind,
        "verified_version": capability.verified_version,
        "rollback_version": capability.rollback_version,
        "observed_version": observed,
        "version_accepted": _version_matches(capability, observed),
        "lifecycle": capability.lifecycle,
        "evidence": capability.evidence,
    }


def _opencode_state(details: str) -> str:
    """Read exactly the native adapter's two status fields."""
    components = {
        line.split(":", 1)[0]: line.split(":", 1)[1].strip().split(" ", 1)[0]
        for line in details.splitlines()
        if line.startswith(("config: ", "plugin: "))
    }
    if components == {"config": "installed", "plugin": "installed"}:
        return "installed"
    if components == {"config": "absent", "plugin": "absent"}:
        return "absent"
    return "partial"


def _command(args: argparse.Namespace) -> int:
    """Execute one read-only inventory or explicit host package operation."""
    if args.action == "list":
        print(json.dumps([_row(item, _host_version(_binary(args, item))) for item in HOSTS]))
        return 0
    if not args.host:
        print("integration host is required for this action", file=sys.stderr)
        return 2
    try:
        capability = host_capability(args.host)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    binary = _binary(args, capability)
    observed = _host_version(binary)
    row = _row(capability, observed)
    row["action"] = args.action
    if args.action not in capability.lifecycle:
        row["state"] = "unsupported"
        row["reason"] = "this host has no admitted package lifecycle for this operation"
        print(json.dumps(row, sort_keys=True))
        return 2
    if args.action in {"install", "diagnose"} and not _version_matches(capability, observed):
        row["state"] = "unverified_version"
        print(json.dumps(row, sort_keys=True))
        return 2
    if capability.key == "codex-cli":
        if binary is None:
            row["state"] = "host_unavailable"
            print(json.dumps(row, sort_keys=True))
            return 2
        try:
            result = apply_codex_package(
                args.action,
                profile=_profile(args, capability),
                host=binary,
                synapse_bin=Path(args.synapse_bin) if args.synapse_bin else None,
                identity=args.identity,
                uri=args.uri,
                token_file=Path(args.token_file) if args.token_file else None,
            )
        except CodexPackageError as exc:
            row["state"] = "error"
            row["reason"] = str(exc)
            print(json.dumps(row, sort_keys=True))
            return 2
        row["state"] = result.state
    elif capability.key in {"claude-code", "opencode"}:
        code, details = _child_adapter(args, capability)
        if capability.key == "claude-code" and code == 0:
            try:
                row["state"] = json.loads(details)["state"]
            except (KeyError, TypeError, ValueError):
                row["state"] = "error"
                code = 2
        elif capability.key == "opencode" and args.action in {"inspect", "diagnose"}:
            row["state"] = _opencode_state(details)
            if args.action == "diagnose" and row["state"] != "installed":
                code = 1
        else:
            row["state"] = "ok" if code == 0 else "error"
        row["details"] = details
        if code:
            print(json.dumps(row, sort_keys=True))
            return code
    elif capability.key == "pi":
        if args.action == "diagnose":
            try:
                if binary is None:
                    raise ValueError("pi executable is unavailable")
                asyncio.run(_pi_diagnose(args, binary))
            except (OSError, ValueError, PiRpcError) as exc:
                row["state"] = "error"
                row["reason"] = str(exc)[:300]
                print(json.dumps(row, sort_keys=True))
                return 2
            row["state"] = "loaded"
        else:
            row["state"] = "bound_participant" if binary is not None else "host_unavailable"
            row["reason"] = "pi loads the claim guard per Synapse participant turn"
    else:
        row["state"] = "unsupported"
    print(json.dumps(row, sort_keys=True))
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the public ``synapse integrations`` review and lifecycle CLI."""
    parser = subparsers.add_parser(
        "integrations", help="Inspect and manage reviewed host packages."
    )
    parser.add_argument("action", choices=("list", "inspect", "install", "diagnose", "uninstall"))
    parser.add_argument("host", nargs="?", help="Host key from `integrations list`.")
    parser.add_argument("--profile-root", default=None)
    parser.add_argument("--host-bin", default=None)
    parser.add_argument("--synapse-bin", default=None)
    parser.add_argument("--identity", default="")
    parser.add_argument("--uri", default=default_hub_uri())
    parser.add_argument("--token-file", default=None)
    parser.add_argument(
        "--pi-model", default=None, help="Locally configured model for offline RPC startup."
    )
    parser.add_argument(
        "--pi-extension", default=None, help="Exact existing Pi claim-guard extension file."
    )
    parser.set_defaults(func=_command)
