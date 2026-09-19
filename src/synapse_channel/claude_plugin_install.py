# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — versioned Claude Code plugin installation custody
"""Render and install an optional Claude Code plugin without touching settings."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

from synapse_channel.cli_claim_hook_common import resolve_synapse_binary
from synapse_channel.cli_claude_claim_hook import render_hook_config
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.secret_files import read_secret_file

PLUGIN_NAME = "synapse-channel"
PLUGIN_VERSION = "0.1.0"
MIN_CORE_VERSION = "0.99.26"
TESTED_HOST_VERSION = "2.1.278"
_MARKER = ".synapse-install.json"
_FILES = (".claude-plugin/plugin.json", "README.md", "hooks/hooks.json", ".mcp.json")


class ClaudePluginInstallError(SynapseError, ValueError):
    """An installation path or owned file failed a reversible-operation check."""

    code = "claude_plugin_install"


@dataclass(frozen=True)
class PluginInspection:
    """Observed plugin state without exposing configured credentials."""

    state: str
    version: str | None
    path: Path


def plugin_path(config_root: Path) -> Path:
    """Return the skills-directory plugin target for an isolated or user profile."""
    return config_root.expanduser() / "skills" / PLUGIN_NAME


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _asset(relative: str) -> bytes:
    root = resources.files("synapse_channel")
    for part in ("integrations", "claude_code", *relative.split("/")):
        root = root.joinpath(part)
    return root.read_bytes()


def render_plugin(
    *, identity: str, uri: str, token_file: Path | None, synapse_bin: str | None
) -> dict[str, bytes]:
    """Render fixed plugin files around the existing MCP and claim-hook commands."""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", identity) is None:
        raise ClaudePluginInstallError("identity must be an exact project/seat name")
    parsed_uri = urlsplit(uri)
    if (
        parsed_uri.scheme not in {"ws", "wss"}
        or not parsed_uri.hostname
        or parsed_uri.username is not None
        or parsed_uri.password is not None
        or parsed_uri.fragment
    ):
        raise ClaudePluginInstallError(
            "hub URI needs a host and ws:// or wss:// without credentials"
        )
    binary = resolve_synapse_binary(synapse_bin)
    token_path: str | None = None
    if token_file is not None:
        token_path = str(token_file.expanduser().resolve(strict=True))
        read_secret_file(token_path, flag="--token-file")
    hook = render_hook_config(
        identity=identity,
        uri=uri,
        ready_timeout=2.0,
        token_file=token_path,
        synapse_bin=binary,
    )
    args = ["mcp", "--name", identity, "--uri", uri]
    if token_path is not None:
        args.extend(["--token-file", token_path])
    mcp = {"mcpServers": {"synapse": {"command": binary, "args": args}}}
    return {
        ".claude-plugin/plugin.json": _asset(".claude-plugin/plugin.json"),
        "README.md": _asset("README.md"),
        "hooks/hooks.json": (json.dumps(hook, indent=2, ensure_ascii=False) + "\n").encode(),
        ".mcp.json": (json.dumps(mcp, indent=2, ensure_ascii=False) + "\n").encode(),
    }


def inspect_plugin(config_root: Path) -> PluginInspection:
    """Distinguish absent, owned, modified and foreign plugin directories."""
    target = plugin_path(config_root)
    if target.is_symlink():
        return PluginInspection("foreign", None, target)
    if not target.exists():
        return PluginInspection("absent", None, target)
    if not target.is_dir():
        return PluginInspection("foreign", None, target)
    marker = target / _MARKER
    if marker.is_symlink() or (marker.exists() and not marker.is_file()):
        return PluginInspection("foreign", None, target)
    try:
        if marker.stat().st_size > 65_536:
            raise ValueError("oversized marker")
        state = json.loads(marker.read_text(encoding="utf-8"))
        version = state["version"]
        digests = state["files"]
        if state["schema"] != "synapse-claude-plugin-install.v1":
            raise ValueError("unknown marker schema")
        if not isinstance(version, str) or set(digests) != set(_FILES):
            raise ValueError("invalid marker contents")
    except (OSError, ValueError, KeyError, TypeError):
        return PluginInspection("foreign", None, target)
    actual_entries = {str(path.relative_to(target)) for path in target.rglob("*")}
    expected_entries = set(_FILES) | {_MARKER, ".claude-plugin", "hooks"}
    if actual_entries != expected_entries:
        return PluginInspection("modified", version, target)
    for relative in _FILES:
        path = target / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or _sha256(path.read_bytes()) != digests[relative]
        ):
            return PluginInspection("modified", version, target)
    return PluginInspection("owned", version, target)


def _staging_directory(target: Path, files: dict[str, bytes]) -> Path:
    """Write a complete candidate plugin beside its final path."""
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if target.parent.is_symlink():
        raise ClaudePluginInstallError("skills directory must not be a symlink")
    staged = Path(tempfile.mkdtemp(prefix=f".{PLUGIN_NAME}-", dir=target.parent))
    digests: dict[str, str] = {}
    try:
        for relative, data in files.items():
            path = staged / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            digests[relative] = _sha256(data)
        marker = {
            "schema": "synapse-claude-plugin-install.v1",
            "version": PLUGIN_VERSION,
            "files": digests,
        }
        (staged / _MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    except BaseException:
        shutil.rmtree(staged)
        raise
    return staged


def apply_plugin(
    action: str,
    *,
    config_root: Path,
    identity: str = "",
    uri: str = "",
    token_file: Path | None = None,
    synapse_bin: str | None = None,
    validate: Callable[[Path], bool] | None = None,
) -> PluginInspection:
    """Install, upgrade or remove only a proven unmodified Synapse plugin."""
    current = inspect_plugin(config_root)
    if action == "uninstall":
        if current.state == "absent":
            return current
        if current.state != "owned":
            raise ClaudePluginInstallError("plugin is foreign or modified; manual review required")
        tombstone = Path(tempfile.mkdtemp(prefix=".synapse-removal-", dir=current.path.parent))
        tombstone.rmdir()
        os.replace(current.path, tombstone)
        try:
            shutil.rmtree(tombstone)
        except BaseException:
            os.replace(tombstone, current.path)
            raise
        return inspect_plugin(config_root)
    if action not in {"install", "upgrade"}:
        raise ClaudePluginInstallError("action must be install, upgrade or uninstall")
    if current.state in {"foreign", "modified"}:
        raise ClaudePluginInstallError("plugin is foreign or modified; manual review required")
    if action == "install" and current.state == "owned":
        raise ClaudePluginInstallError("plugin already installed; use upgrade")
    if action == "upgrade" and current.state == "absent":
        raise ClaudePluginInstallError("plugin is absent; use install")
    files = render_plugin(
        identity=identity, uri=uri, token_file=token_file, synapse_bin=synapse_bin
    )
    staged = _staging_directory(current.path, files)
    try:
        if validate is not None and not validate(staged):
            raise ClaudePluginInstallError("Claude Code rejected the staged plugin")
        if current.state == "absent":
            os.replace(staged, current.path)
        else:
            backup = Path(tempfile.mkdtemp(prefix=".synapse-backup-", dir=current.path.parent))
            backup.rmdir()
            os.replace(current.path, backup)
            try:
                os.replace(staged, current.path)
            except BaseException:
                os.replace(backup, current.path)
                raise
            shutil.rmtree(backup)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return inspect_plugin(config_root)
