# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — reversible Codex stdio MCP package custody
"""Manage only a checksum-owned Synapse MCP entry in an exact Codex profile."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from synapse_channel.core.errors import SynapseError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib

TESTED_CODEX_VERSION = "0.156.0"
MARKER_NAME = ".synapse-codex-mcp.json"
OWNER_ENV = "SYNAPSE_ADAPTER_OWNER"
OWNER_VALUE = "synapse-channel"
MAX_CONFIG_BYTES = 1_048_576


class CodexPackageError(SynapseError, ValueError):
    """The selected profile, host or entry failed a reversible operation check."""

    code = "codex_package"


@dataclass(frozen=True)
class CodexPackageInspection:
    """Secret-free observation of a Codex host and its Synapse entry."""

    state: str
    host_version: str
    profile: Path


def _digest(value: dict[str, Any]) -> str:
    """Bind the complete host entry to the separate Synapse ownership marker."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _profile_paths(profile: Path) -> tuple[Path, Path]:
    """Reject symlinked profile/config/marker paths before reading or writing."""
    root = profile.expanduser().absolute()
    config = root / "config.toml"
    marker = root / MARKER_NAME
    if (
        any(part.is_symlink() for part in (root, *root.parents))
        or config.is_symlink()
        or marker.is_symlink()
    ):
        raise CodexPackageError("Codex profile paths must not be symlinks")
    if root.exists() and not root.is_dir():
        raise CodexPackageError("Codex profile root is not a directory")
    for path in (config, marker):
        if path.exists() and (not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES):
            raise CodexPackageError("Codex profile file is invalid or oversized")
    return config, marker


def _entry(profile: Path) -> dict[str, Any] | None:
    """Read the bounded Codex TOML entry without interpreting other profile data."""
    config, _ = _profile_paths(profile)
    if not config.exists():
        return None
    try:
        parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise CodexPackageError("Codex config.toml is invalid") from exc
    servers = parsed.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise CodexPackageError("Codex MCP server table is invalid")
    entry = servers.get("synapse")
    if entry is not None and not isinstance(entry, dict):
        raise CodexPackageError("Codex Synapse MCP entry is invalid")
    return entry


def _run(host: Path, profile: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the pinned host with an isolated explicit profile and no shell."""
    env = dict(os.environ)
    env["CODEX_HOME"] = str(profile.expanduser().absolute())
    try:
        return subprocess.run(
            [str(host), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexPackageError("Codex host command failed") from exc


def _version(host: Path, profile: Path) -> str:
    """Probe the actual executable and refuse an unverified host version."""
    if not host.is_file() or not os.access(host, os.X_OK):
        raise CodexPackageError("Codex executable is unavailable")
    result = _run(host, profile, "--version")
    match = re.fullmatch(r"codex-cli (\d+\.\d+\.\d+)", result.stdout.strip())
    if result.returncode or match is None:
        raise CodexPackageError("Codex version probe failed")
    return match.group(1)


def inspect_codex_package(profile: Path, host: Path) -> CodexPackageInspection:
    """Distinguish absent, owned, modified and foreign Synapse entries."""
    version = _version(host, profile)
    entry = _entry(profile)
    _, marker_path = _profile_paths(profile)
    if entry is None and not marker_path.exists():
        state = "absent"
    elif entry is None or not marker_path.exists():
        state = "foreign"
    else:
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise CodexPackageError("Codex ownership marker is invalid") from exc
        if not isinstance(marker, dict) or marker.get("schema") != "synapse-codex-mcp.v1":
            state = "foreign"
        elif not isinstance(entry.get("env"), dict) or entry["env"].get(OWNER_ENV) != OWNER_VALUE:
            state = "modified"
        else:
            state = "owned" if marker.get("entry_sha256") == _digest(entry) else "modified"
    return CodexPackageInspection(state, version, profile.expanduser().absolute())


def apply_codex_package(
    action: str,
    *,
    profile: Path,
    host: Path,
    synapse_bin: Path | None = None,
    identity: str = "",
    uri: str = "",
    token_file: Path | None = None,
) -> CodexPackageInspection:
    """Install, diagnose or remove an exact-version owned Codex MCP entry."""
    current = inspect_codex_package(profile, host)
    if action == "inspect":
        return current
    if action in {"install", "diagnose"} and current.host_version != TESTED_CODEX_VERSION:
        raise CodexPackageError(f"Codex {current.host_version} is unverified")
    if action == "diagnose":
        if current.state != "owned":
            raise CodexPackageError("Codex Synapse entry is not owned and intact")
        observed = _run(host, current.profile, "mcp", "get", "synapse", "--json")
        if observed.returncode:
            raise CodexPackageError("Codex MCP validator refused the entry")
        try:
            decoded = json.loads(observed.stdout)
        except ValueError as exc:
            raise CodexPackageError("Codex MCP validator returned invalid JSON") from exc
        if not isinstance(decoded, dict) or not isinstance(decoded.get("transport"), dict):
            raise CodexPackageError("Codex MCP validator returned an invalid entry")
        transport = decoded["transport"]
        entry = _entry(profile)
        if (
            transport.get("type") != "stdio"
            or entry is None
            or transport.get("command") != entry.get("command")
            or transport.get("args") != entry.get("args")
        ):
            raise CodexPackageError("Codex MCP transport is not stdio")
        return current
    _, marker_path = _profile_paths(profile)
    if action == "uninstall":
        if current.state == "absent":
            return current
        if current.state != "owned":
            raise CodexPackageError("Codex Synapse entry is foreign or modified")
        result = _run(host, current.profile, "mcp", "remove", "synapse")
        if result.returncode or _entry(profile) is not None:
            raise CodexPackageError("Codex MCP removal did not complete")
        marker_path.unlink()
        return inspect_codex_package(profile, host)
    if action != "install":
        raise CodexPackageError("unknown Codex package action")
    if current.state != "absent":
        raise CodexPackageError("Codex Synapse entry already exists")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", identity) is None:
        raise CodexPackageError("identity must be an exact project/seat name")
    parsed_uri = urlsplit(uri)
    if (
        parsed_uri.scheme not in {"ws", "wss"}
        or not parsed_uri.hostname
        or parsed_uri.username is not None
        or parsed_uri.password is not None
        or parsed_uri.fragment
    ):
        raise CodexPackageError("hub URI is invalid")
    if synapse_bin is None or not synapse_bin.is_file() or not os.access(synapse_bin, os.X_OK):
        raise CodexPackageError("Synapse executable is unavailable")
    if token_file is not None:
        if not token_file.is_file():
            raise CodexPackageError("token file is unavailable")
        mode = token_file.stat()
        if mode.st_uid != os.getuid() or mode.st_mode & 0o077:
            raise CodexPackageError("token file must be owner-only")
    current.profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    command = [str(synapse_bin), "mcp", "--name", identity, "--uri", uri]
    if token_file is not None:
        command.extend(["--token-file", str(token_file)])
    result = _run(
        host,
        current.profile,
        "mcp",
        "add",
        "--env",
        f"{OWNER_ENV}={OWNER_VALUE}",
        "synapse",
        "--",
        *command,
    )
    if result.returncode:
        raise CodexPackageError("Codex MCP installation failed")
    entry = _entry(profile)
    if entry is None or entry.get("env", {}).get(OWNER_ENV) != OWNER_VALUE:
        raise CodexPackageError("Codex MCP installation produced an unexpected entry")
    marker = {"schema": "synapse-codex-mcp.v1", "entry_sha256": _digest(entry)}
    try:
        descriptor = os.open(marker_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(json.dumps(marker, sort_keys=True) + "\n")
    except OSError as exc:
        _run(host, current.profile, "mcp", "remove", "synapse")
        raise CodexPackageError("Codex marker creation failed; entry removal attempted") from exc
    return inspect_codex_package(profile, host)
