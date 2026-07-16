# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — strict schema for outbound MCP execution policy
"""Typed, fail-closed schema for outbound MCP execution policy.

The outbound MCP document is executable policy: it selects a process, its
arguments, working directory, environment, and callable tools. This module only
parses that document into immutable values. Filesystem provenance, signatures,
and executable pins live in :mod:`synapse_channel.core.mcp_config_trust`; process
launching stays in :mod:`synapse_channel.core.mcp_outbound`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from synapse_channel.core.errors import SynapseError

MCP_CONFIG_VERSION = 1
"""Current outbound MCP policy document version."""

WILDCARD = "*"
"""Tool allowlist token that admits every advertised tool for one server."""

_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")

_SERVER_FIELDS = frozenset(
    {
        "allowed_tools",
        "args",
        "command",
        "command_sha256",
        "cwd",
        "env",
        "inherit_env",
        "name",
        "timeout_seconds",
    }
)
_DOCUMENT_FIELDS = frozenset({"servers", "signature", "version"})


class McpConfigError(SynapseError, ValueError):
    """Raised when outbound MCP execution policy is malformed or untrusted."""

    code = "mcp_config"


@dataclass(frozen=True)
class McpServerSpec:
    """One outbound MCP server admitted by operator policy.

    Parameters
    ----------
    name : str
        Stable server name referenced on the command line.
    command : str
        Absolute executable path. The trust layer proves it before launch.
    args : tuple[str, ...], optional
        Literal executable arguments.
    env : Mapping[str, str], optional
        Explicit environment values passed to the child.
    cwd : str, optional
        Absolute working directory. Empty low-level specs bind to ``/``.
    allowed_tools : frozenset[str], optional
        Tool names this server may run, or ``{"*"}`` for every tool.
    timeout_seconds : float, optional
        Positive finite startup and discovery/invocation deadline. MCP SDK cleanup
        uses its separately audited two-second termination window.
    inherit_env : tuple[str, ...], optional
        Parent environment names the operator explicitly approves. Nothing is
        inherited when this tuple is empty. Appended after the legacy fields to
        preserve positional construction compatibility.
    command_sha256 : str, optional
        Lower-case SHA-256 pin for the executable, or empty for an absolute-path
        pin only.
    """

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str = ""
    allowed_tools: frozenset[str] = frozenset()
    timeout_seconds: float = 30.0
    inherit_env: tuple[str, ...] = ()
    command_sha256: str = ""

    def __post_init__(self) -> None:
        """Defensively freeze nested policy values retained from callers."""
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))
        object.__setattr__(self, "allowed_tools", frozenset(self.allowed_tools))
        object.__setattr__(self, "inherit_env", tuple(self.inherit_env))


def tool_allowed(spec: McpServerSpec, tool: str) -> bool:
    """Return whether ``tool`` is permitted on ``spec`` (deny by default)."""
    return WILDCARD in spec.allowed_tools or tool in spec.allowed_tools


def parse_mcp_config(document: object) -> dict[str, McpServerSpec]:
    """Parse one decoded outbound MCP policy document.

    Parameters
    ----------
    document : object
        JSON-compatible value decoded with duplicate-key rejection.

    Returns
    -------
    dict[str, McpServerSpec]
        Immutable server specifications keyed by their unique names.

    Raises
    ------
    McpConfigError
        If the document version, top-level shape, field types, or a server entry
        is invalid. Unknown fields fail closed so misspelled controls cannot be
        silently ignored.
    """
    if not isinstance(document, dict):
        raise McpConfigError("MCP config must be a JSON object")
    unknown = set(document) - _DOCUMENT_FIELDS
    if unknown:
        raise McpConfigError(f"MCP config has unknown field(s): {_field_list(unknown)}")
    version = document.get("version", MCP_CONFIG_VERSION)
    if isinstance(version, bool) or not isinstance(version, int) or version != MCP_CONFIG_VERSION:
        raise McpConfigError(f"MCP config version must be {MCP_CONFIG_VERSION}")
    entries = document.get("servers")
    if not isinstance(entries, list):
        raise McpConfigError("MCP config must contain a 'servers' list")
    servers: dict[str, McpServerSpec] = {}
    for index, entry in enumerate(entries):
        spec = _parse_server(entry, index)
        if spec.name in servers:
            raise McpConfigError(f"duplicate MCP server name: {spec.name}")
        servers[spec.name] = spec
    return servers


def _parse_server(entry: object, index: int) -> McpServerSpec:
    """Parse one strict server entry."""
    if not isinstance(entry, dict):
        raise McpConfigError(f"MCP server entry {index} must be an object")
    unknown = set(entry) - _SERVER_FIELDS
    if unknown:
        raise McpConfigError(
            f"MCP server entry {index} has unknown field(s): {_field_list(unknown)}"
        )
    name = _required_text(entry, "name", index)
    command = _required_text(entry, "command", index)
    args = _text_sequence(entry.get("args", []), field_name="args", index=index)
    inherit_env = _environment_names(entry.get("inherit_env", []), index=index)
    allowed_tools = frozenset(
        _text_sequence(entry.get("allowed_tools", []), field_name="allowed_tools", index=index)
    )
    env_value = entry.get("env", {})
    if not isinstance(env_value, dict):
        raise McpConfigError(f"MCP server entry {index} field 'env' must be an object")
    env: dict[str, str] = {}
    for key, value in env_value.items():
        if not isinstance(key, str) or _ENVIRONMENT_NAME.fullmatch(key) is None:
            raise McpConfigError(f"MCP server entry {index} has invalid environment name {key!r}")
        if not isinstance(value, str):
            raise McpConfigError(
                f"MCP server entry {index} environment value for {key!r} must be a string"
            )
        env[key] = value
    cwd_value = entry.get("cwd", "")
    if not isinstance(cwd_value, str):
        raise McpConfigError(f"MCP server entry {index} field 'cwd' must be a string")
    timeout = entry.get("timeout_seconds", 30.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise McpConfigError(f"MCP server entry {index} field 'timeout_seconds' must be a number")
    timeout_seconds = float(timeout)
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0.0:
        raise McpConfigError(
            f"MCP server entry {index} field 'timeout_seconds' must be positive and finite"
        )
    digest_value = entry.get("command_sha256", "")
    if not isinstance(digest_value, str):
        raise McpConfigError(f"MCP server entry {index} field 'command_sha256' must be a string")
    command_sha256 = digest_value.strip().lower()
    if command_sha256 and _SHA256_HEX.fullmatch(command_sha256) is None:
        raise McpConfigError(
            f"MCP server entry {index} field 'command_sha256' must be 64 hexadecimal characters"
        )
    return McpServerSpec(
        name=name,
        command=command,
        args=args,
        env=env,
        inherit_env=inherit_env,
        cwd=cwd_value.strip(),
        allowed_tools=allowed_tools,
        timeout_seconds=timeout_seconds,
        command_sha256=command_sha256,
    )


def _required_text(entry: dict[str, Any], field_name: str, index: int) -> str:
    """Return one non-empty string field."""
    value = entry.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise McpConfigError(
            f"MCP server entry {index} needs non-empty string field {field_name!r}"
        )
    return value.strip()


def _text_sequence(value: object, *, field_name: str, index: int) -> tuple[str, ...]:
    """Return one exact string list as a tuple."""
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise McpConfigError(
            f"MCP server entry {index} field {field_name!r} must be a list of strings"
        )
    if any(not item for item in value):
        raise McpConfigError(
            f"MCP server entry {index} field {field_name!r} must not contain empty strings"
        )
    if len(value) != len(set(value)):
        raise McpConfigError(
            f"MCP server entry {index} field {field_name!r} must not contain duplicates"
        )
    return tuple(value)


def _environment_names(value: object, *, index: int) -> tuple[str, ...]:
    """Parse the explicit parent-environment allowlist."""
    names = _text_sequence(value, field_name="inherit_env", index=index)
    invalid = [name for name in names if _ENVIRONMENT_NAME.fullmatch(name) is None]
    if invalid:
        raise McpConfigError(
            f"MCP server entry {index} has invalid inherited environment name {invalid[0]!r}"
        )
    return names


def _field_list(fields: set[object]) -> str:
    """Render unknown JSON fields deterministically."""
    return ", ".join(repr(field) for field in sorted(fields, key=repr))
