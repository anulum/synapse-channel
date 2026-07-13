# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pure OpenCode adapter configuration planner
"""Plan owned OpenCode MCP and native-plugin installation without filesystem I/O."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.errors import SynapseError
from synapse_channel.opencode_plugin import PLUGIN_OWNER_MARKER

ADAPTER_OWNER = "synapse-channel"
MCP_KEY = "synapse"
PLUGIN_FILENAME = "synapse-claim-guard.js"
DEFAULT_MCP_TIMEOUT_MS = 30_000
MAX_CONFIG_BYTES = 1_048_576


class OpenCodeAdapterError(SynapseError, ValueError):
    """An OpenCode config or plugin cannot be transformed safely."""

    code = "opencode_adapter"


@dataclass(frozen=True)
class OpenCodePaths:
    """Config and plugin paths owned by one OpenCode installation scope."""

    config: Path
    plugin: Path


def resolve_opencode_paths(
    *,
    scope: str,
    project: Path,
    home: Path,
    config_root: Path | None = None,
) -> OpenCodePaths:
    """Resolve project or global OpenCode adapter paths."""
    if scope == "project":
        base = project.expanduser().resolve() / ".opencode"
    elif scope == "global":
        configured = os.environ.get("XDG_CONFIG_HOME", "").strip()
        root = config_root or (Path(configured) if configured else home.expanduser() / ".config")
        base = root.expanduser().resolve() / "opencode"
    else:
        raise OpenCodeAdapterError("OpenCode scope must be 'project' or 'global'.")
    return OpenCodePaths(config=base / "opencode.json", plugin=base / "plugins" / PLUGIN_FILENAME)


def build_mcp_entry(
    *,
    synapse_bin: str,
    identity: str,
    uri: str,
    token_file: str | None,
    timeout_ms: int = DEFAULT_MCP_TIMEOUT_MS,
) -> dict[str, Any]:
    """Return an owned local-stdio MCP entry for OpenCode."""
    if not synapse_bin or not identity.strip() or not uri.strip():
        raise OpenCodeAdapterError("Synapse binary, identity, and hub URI are required.")
    if not isinstance(timeout_ms, int) or timeout_ms <= 0:
        raise OpenCodeAdapterError("OpenCode MCP timeout must be a positive integer.")
    command = [synapse_bin, "mcp", "--name", identity, "--uri", uri]
    if token_file:
        command.extend(["--token-file", token_file])
    return {
        "type": "local",
        "command": command,
        "enabled": True,
        "environment": {"SYNAPSE_ADAPTER_OWNER": ADAPTER_OWNER},
        "timeout": timeout_ms,
    }


def parse_config(text: str) -> dict[str, Any]:
    """Parse one bounded strict-JSON OpenCode config object."""
    if len(text.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise OpenCodeAdapterError("OpenCode config exceeds the automatic-edit size limit.")
    if not text.strip():
        return {}
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OpenCodeAdapterError(
            "OpenCode adapter config is not strict JSON; refusing to rewrite JSONC."
        ) from exc
    if not isinstance(decoded, dict):
        raise OpenCodeAdapterError("OpenCode adapter config must be a JSON object.")
    return decoded


def _owned(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    environment = entry.get("environment")
    return (
        isinstance(environment, dict) and environment.get("SYNAPSE_ADAPTER_OWNER") == ADAPTER_OWNER
    )


def plan_config_install(existing: str, entry: dict[str, Any]) -> str:
    """Install or update only the owned ``mcp.synapse`` entry."""
    config = parse_config(existing)
    mcp = config.get("mcp")
    if mcp is None:
        mcp = {}
        config["mcp"] = mcp
    if not isinstance(mcp, dict):
        raise OpenCodeAdapterError("OpenCode config field 'mcp' must be an object.")
    current = mcp.get(MCP_KEY)
    if current is not None and not _owned(current):
        raise OpenCodeAdapterError(
            "OpenCode mcp.synapse exists but is not Synapse-owned; refusing to overwrite it."
        )
    mcp[MCP_KEY] = entry
    return json.dumps(config, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def plan_config_uninstall(existing: str) -> str | None:
    """Remove only an owned ``mcp.synapse`` entry, preserving user config."""
    config = parse_config(existing)
    mcp = config.get("mcp")
    if mcp is None:
        return existing or None
    if not isinstance(mcp, dict):
        raise OpenCodeAdapterError("OpenCode config field 'mcp' must be an object.")
    current = mcp.get(MCP_KEY)
    if current is None:
        return existing or None
    if not _owned(current):
        raise OpenCodeAdapterError(
            "OpenCode mcp.synapse is not Synapse-owned; refusing to remove it."
        )
    del mcp[MCP_KEY]
    if not mcp:
        del config["mcp"]
    if not config:
        return None
    return json.dumps(config, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def plugin_is_owned(text: str) -> bool:
    """Return whether ``text`` starts with the exact Synapse plugin marker."""
    return text.splitlines()[:1] == [f"// {PLUGIN_OWNER_MARKER}"]


def plan_plugin_install(existing: str, rendered: str) -> str:
    """Install an owned plugin, refusing to replace an unowned file."""
    if existing and not plugin_is_owned(existing):
        raise OpenCodeAdapterError("OpenCode plugin path exists but is not Synapse-owned.")
    if not plugin_is_owned(rendered):
        raise OpenCodeAdapterError("Rendered OpenCode plugin lacks its ownership marker.")
    return rendered


def plan_plugin_uninstall(existing: str) -> None:
    """Validate that an existing plugin may be removed."""
    if existing and not plugin_is_owned(existing):
        raise OpenCodeAdapterError("OpenCode plugin path exists but is not Synapse-owned.")
