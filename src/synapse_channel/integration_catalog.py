# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — explicit host integration capability catalog
"""Version-bound host capabilities without implicit promotion from discovery."""

from __future__ import annotations

from dataclasses import dataclass

from synapse_channel.claude_plugin_install import TESTED_HOST_VERSION as CLAUDE_VERSION
from synapse_channel.codex_mcp_package import TESTED_CODEX_VERSION
from synapse_channel.participants.opencode_stream import OPENCODE_SCHEMA_VERSION
from synapse_channel.participants.pi_rpc import PI_RPC_VERSION


@dataclass(frozen=True)
class HostCapability:
    """A reviewed host surface and the lifecycle Synapse can actually govern."""

    key: str
    name: str
    capability: str
    verified_version: str | None
    rollback_version: str | None
    package_kind: str
    lifecycle: tuple[str, ...]
    evidence: str


HOSTS: tuple[HostCapability, ...] = (
    HostCapability(
        "claude-code",
        "Claude Code",
        "native",
        CLAUDE_VERSION,
        "2.1.278",
        "claude-plugin",
        ("inspect", "install", "diagnose", "uninstall"),
        "strict plugin validator and isolated Linux hub/claim journey",
    ),
    HostCapability(
        "codex-cli",
        "Codex CLI",
        "manual",
        TESTED_CODEX_VERSION,
        None,
        "stdio-mcp",
        ("inspect", "install", "diagnose", "uninstall"),
        "isolated Linux app-server and stdio MCP hub/claim journey",
    ),
    HostCapability(
        "pi",
        "Pi",
        "native",
        PI_RPC_VERSION,
        "0.86.0",
        "bound-participant",
        ("inspect", "diagnose"),
        "isolated RPC, extension load and local hub/claim journey",
    ),
    HostCapability(
        "opencode",
        "OpenCode",
        "native",
        OPENCODE_SCHEMA_VERSION,
        "1.18.31",
        "opencode-adapter",
        ("inspect", "install", "diagnose", "uninstall"),
        "isolated JSONL, ACP, server and local hub/claim journey",
    ),
    HostCapability(
        "gemini-cli",
        "Gemini CLI",
        "unsupported",
        None,
        None,
        "none",
        ("inspect",),
        "packaged extension has no accepted exact-version host journey",
    ),
    HostCapability(
        "claude-desktop",
        "Claude Desktop",
        "unsupported",
        None,
        None,
        "none",
        ("inspect",),
        "no validated Desktop package or host lifecycle in this checkout",
    ),
)

_BY_KEY = {item.key: item for item in HOSTS}


def host_capability(key: str) -> HostCapability:
    """Return one declared host or raise a clear unknown-host error."""
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        raise ValueError(f"unknown integration host: {key}") from exc
