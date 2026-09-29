# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — doctor checks for host MCP server definitions (K4-MCP-INBOUND)
"""Find the Synapse-named MCP servers a host would start, and whether edits are guarded.

An MCP client cannot tell the genuine ``synapse mcp`` server from an impostor that
reports the same server name and tools (reproduced for K4-MCP-INBOUND): the
impostor's "claim granted" is a reply the hub never saw. Two static, read-only
checks make that visible:

* ``mcp-host-sources`` lists every server definition whose name contains
  ``synapse`` in the host sources Synapse documents — Claude Code user and local
  scope (``.claude.json``), project scope (``.mcp.json``), the Synapse Claude plugin,
  and the Codex profile — and warns about any that does not launch this
  installation's ``synapse mcp``;
* ``mcp-claim-guard`` warns when a host has such a server but no claim guard,
  because only the guard, which asks the hub itself, stops a false grant from
  becoming an unguarded edit.

Both report configuration, not runtime: they show what a host is set up to start,
not what it started. A server whose name does not contain ``synapse``, a replaced
``synapse`` executable, and hosts other than Claude Code and Codex are out of scope.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from synapse_channel.claude_plugin_install import inspect_plugin
from synapse_channel.cli_claim_hook_common import resolve_synapse_binary
from synapse_channel.client.diagnostics import Diagnosis
from synapse_channel.mutation_governance import inspect_mutation_governance

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib

DefinitionStatus = Literal["genuine", "other-install", "foreign", "remote"]

_READ_LIMIT = 16 * 1_048_576
_HOST_LABELS = {"claude": "Claude Code", "codex": "Codex"}
_GUARD_RECIPES = {
    "claude": (
        "synapse claude-plugin install, or merge `synapse adapters claude-claim-hook "
        "--identity PROJECT/agent --print-config` into .claude/settings.json"
    ),
    "codex": (
        "merge `synapse adapters codex-claim-hook --identity PROJECT/agent --print-config` "
        "into ~/.codex/hooks.json and trust it with /hooks"
    ),
}


@dataclass(frozen=True)
class McpServerDefinition:
    """One MCP server definition a host would start.

    Attributes
    ----------
    host : str
        ``"claude"`` or ``"codex"``.
    scope : str
        Where the host reads it: ``user``, ``local``, ``project``, ``plugin`` or ``profile``.
    source : pathlib.Path
        The configuration file (or plugin directory) holding it.
    name : str
        The server name the host shows.
    command : str or None
        The executable of a stdio server.
    args : tuple[str, ...]
        Its arguments.
    url : str or None
        The endpoint of a remote server.
    plugin_state : str or None
        For the Synapse Claude plugin, its custody state (``owned``, ``modified``,
        ``foreign``); ``None`` for every other definition.
    """

    host: str
    scope: str
    source: Path
    name: str
    command: str | None
    args: tuple[str, ...]
    url: str | None
    plugin_state: str | None = None


@dataclass(frozen=True)
class ClassifiedDefinition:
    """A definition and whether it launches this installation's ``synapse mcp``."""

    definition: McpServerDefinition
    status: DefinitionStatus
    reason: str


@dataclass(frozen=True)
class HostSourceScan:
    """Everything the scan found: definitions, unreadable sources, inspected paths."""

    definitions: tuple[McpServerDefinition, ...]
    problems: tuple[str, ...]
    inspected: tuple[Path, ...]


def default_project(cwd: Path, *, search_path: str | None = None) -> Path:
    """Return the Git top level containing ``cwd``, or ``cwd`` outside a worktree.

    ``search_path`` is the ``PATH`` used to find ``git`` (the process ``PATH`` by
    default); without a runnable ``git`` the answer is ``cwd``.
    """
    git = shutil.which("git", path=search_path)
    if git is None:
        return cwd
    try:
        result = subprocess.run(  # nosec B603
            [git, "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return cwd
    top = result.stdout.strip()
    return Path(top) if result.returncode == 0 and top else cwd


def _read_bounded(path: Path) -> str:
    """Read a configuration file of at most 16 MiB as UTF-8."""
    if path.stat().st_size > _READ_LIMIT:
        raise ValueError(f"larger than {_READ_LIMIT} bytes")
    return path.read_text(encoding="utf-8")


def _entry(
    host: str, scope: str, source: Path, name: str, raw: object
) -> McpServerDefinition | None:
    """Turn one raw server entry into a definition, or ``None`` when out of scope."""
    if "synapse" not in name.lower():
        return None
    body: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    command = body.get("command")
    args = body.get("args")
    url = body.get("url")
    return McpServerDefinition(
        host=host,
        scope=scope,
        source=source,
        name=name,
        command=command if isinstance(command, str) else None,
        args=tuple(str(item) for item in args) if isinstance(args, list) else (),
        url=url if isinstance(url, str) else None,
    )


def _entries(host: str, scope: str, source: Path, servers: object) -> list[McpServerDefinition]:
    if not isinstance(servers, Mapping):
        return []
    found: list[McpServerDefinition] = []
    for name, raw in servers.items():
        definition = _entry(host, scope, source, str(name), raw)
        if definition is not None:
            found.append(definition)
    return found


def scan_host_sources(*, home: Path, project: Path, env: Mapping[str, str]) -> HostSourceScan:
    """Collect Synapse-named server definitions from the documented host sources.

    Parameters
    ----------
    home : pathlib.Path
        The user's home directory.
    project : pathlib.Path
        The project whose local and project scopes apply.
    env : Mapping[str, str]
        Environment; ``CLAUDE_CONFIG_DIR`` and ``CODEX_HOME`` relocate the host files.

    Returns
    -------
    HostSourceScan
        Definitions in source order, readable problems, and every path inspected.
    """
    definitions: list[McpServerDefinition] = []
    problems: list[str] = []
    inspected: list[Path] = []
    project_key = str(project)

    claude_files = [home / ".claude.json"]
    claude_root = Path(env["CLAUDE_CONFIG_DIR"]) if env.get("CLAUDE_CONFIG_DIR") else None
    if claude_root is not None:
        claude_files.append(claude_root / ".claude.json")
    json_sources: list[tuple[str, Path]] = [("claude-user", path) for path in claude_files]
    json_sources.append(("claude-project", project / ".mcp.json"))
    for kind, path in json_sources:
        inspected.append(path)
        if not path.is_file():
            continue
        try:
            data = json.loads(_read_bounded(path))
        except (OSError, UnicodeError, ValueError) as exc:
            problems.append(f"cannot inspect {path}: {exc}")
            continue
        if not isinstance(data, Mapping):
            problems.append(f"cannot inspect {path}: not a JSON object")
            continue
        if kind == "claude-project":
            definitions.extend(_entries("claude", "project", path, data.get("mcpServers")))
            continue
        definitions.extend(_entries("claude", "user", path, data.get("mcpServers")))
        projects = data.get("projects")
        if isinstance(projects, Mapping):
            local = projects.get(project_key)
            if isinstance(local, Mapping):
                definitions.extend(_entries("claude", "local", path, local.get("mcpServers")))

    plugin_root = claude_root if claude_root is not None else home / ".claude"
    plugin = inspect_plugin(plugin_root)
    inspected.append(plugin.path)
    if plugin.state != "absent":
        definitions.append(
            McpServerDefinition(
                host="claude",
                scope="plugin",
                source=plugin.path,
                name="plugin:synapse-channel",
                command=None,
                args=(),
                url=None,
                plugin_state=plugin.state,
            )
        )

    codex_home = Path(env["CODEX_HOME"]) if env.get("CODEX_HOME") else home / ".codex"
    codex_config = codex_home / "config.toml"
    inspected.append(codex_config)
    if codex_config.is_file():
        try:
            parsed = tomllib.loads(_read_bounded(codex_config))
        except (OSError, UnicodeError, ValueError) as exc:
            problems.append(f"cannot inspect {codex_config}: {exc}")
        else:
            definitions.extend(
                _entries("codex", "profile", codex_config, parsed.get("mcp_servers"))
            )
    return HostSourceScan(tuple(definitions), tuple(problems), tuple(inspected))


def classify_definition(
    definition: McpServerDefinition,
    *,
    synapse_binary: Path | None,
    python_executable: Path,
    which: Callable[[str], str | None],
) -> ClassifiedDefinition:
    """Decide whether one definition launches this installation's ``synapse mcp``.

    Parameters
    ----------
    definition : McpServerDefinition
        The definition to judge.
    synapse_binary : pathlib.Path or None
        This installation's ``synapse`` executable, when it can be resolved.
    python_executable : pathlib.Path
        The interpreter running this installation (``python -m synapse_channel``).
    which : Callable[[str], str or None]
        Resolves a bare command name the way the host's ``PATH`` would.

    Returns
    -------
    ClassifiedDefinition
        ``genuine``, ``other-install`` (a different ``synapse`` or Python), ``foreign``
        (anything else), or ``remote`` (an endpoint this check cannot open).
    """
    if definition.plugin_state is not None:
        if definition.plugin_state == "owned":
            return ClassifiedDefinition(
                definition, "genuine", "checksum-owned Synapse plugin, unmodified"
            )
        return ClassifiedDefinition(
            definition,
            "foreign",
            f"the Synapse plugin directory is {definition.plugin_state}: it no longer "
            "matches its installed checksums",
        )
    if definition.command is None:
        if definition.url is not None:
            return ClassifiedDefinition(
                definition,
                "remote",
                f"remote endpoint {definition.url}; its TLS certificate and bearer grant "
                "authenticate it, not this check",
            )
        return ClassifiedDefinition(definition, "foreign", "defines no command")
    command = definition.command
    located = command if os.path.isabs(command) else which(command)
    if located is None:
        return ClassifiedDefinition(
            definition, "foreign", f"command {command!r} does not resolve on PATH"
        )
    resolved = Path(located).resolve()
    args = definition.args
    module_form = args[:2] == ("-m", "synapse_channel")
    rest = args[2:] if module_form else args
    if rest[:1] != ("mcp",):
        return ClassifiedDefinition(
            definition, "foreign", f"runs {resolved} {' '.join(args)}, not `synapse mcp`".strip()
        )
    if module_form:
        if resolved == python_executable.resolve():
            return ClassifiedDefinition(definition, "genuine", "this interpreter's synapse_channel")
        return ClassifiedDefinition(
            definition, "other-install", f"python -m synapse_channel under {resolved}"
        )
    if synapse_binary is not None and resolved == synapse_binary.resolve():
        return ClassifiedDefinition(definition, "genuine", f"this installation's {resolved}")
    if resolved.name == "synapse":
        return ClassifiedDefinition(
            definition, "other-install", f"a different synapse executable at {resolved}"
        )
    return ClassifiedDefinition(definition, "foreign", f"launches {resolved}, not synapse")


def _label(item: ClassifiedDefinition) -> str:
    definition = item.definition
    host = _HOST_LABELS.get(definition.host, definition.host)
    return f"{host} {definition.scope} '{definition.name}' ({definition.source}): {item.reason}"


def check_mcp_host_sources(
    scan: HostSourceScan, classified: tuple[ClassifiedDefinition, ...]
) -> Diagnosis:
    """Summarise the scan: warn on unreadable sources and on non-genuine definitions."""
    doubtful = [item for item in classified if item.status in ("foreign", "other-install")]
    if scan.problems or doubtful:
        findings = [*scan.problems, *(_label(item) for item in doubtful)]
        return Diagnosis(
            check="mcp-host-sources",
            status="warn",
            detail="; ".join(findings),
            remedy=(
                "remove or correct each listed server: an MCP server that calls itself "
                "synapse can answer 'claim granted' while the hub holds nothing "
                "(claude mcp remove NAME -s SCOPE, or edit the Codex config.toml)"
            ),
        )
    if not classified:
        return Diagnosis(
            check="mcp-host-sources",
            status="pass",
            detail=f"no Synapse-named MCP server in {len(scan.inspected)} inspected source(s)",
            remedy="",
        )
    remote = sum(1 for item in classified if item.status == "remote")
    genuine = len(classified) - remote
    return Diagnosis(
        check="mcp-host-sources",
        status="pass",
        detail=(
            f"{genuine} Synapse MCP definition(s) launch this installation"
            + (f"; {remote} remote endpoint(s) not checked here" if remote else "")
        ),
        remedy="",
    )


def check_mcp_claim_guard(
    classified: tuple[ClassifiedDefinition, ...],
    *,
    guard_states: Mapping[str, tuple[str, str]],
) -> Diagnosis:
    """Warn when a host with a Synapse MCP server has no claim guard configured.

    Parameters
    ----------
    classified : tuple[ClassifiedDefinition, ...]
        Every Synapse-named definition found.
    guard_states : Mapping[str, tuple[str, str]]
        Per host, the mutation-governance configuration state and its detail.
    """
    hosts = sorted({item.definition.host for item in classified})
    if not hosts:
        return Diagnosis(
            check="mcp-claim-guard",
            status="pass",
            detail="no host has a Synapse MCP server configured",
            remedy="",
        )
    unguarded: list[str] = []
    remedies: list[str] = []
    for host in hosts:
        state, detail = guard_states.get(host, ("not-configured", "not inspected"))
        if state == "configured":
            continue
        label = _HOST_LABELS.get(host, host)
        unguarded.append(f"{label} ({state}: {detail})")
        remedies.append(f"{label}: {_GUARD_RECIPES.get(host, 'install its claim guard')}")
    if not unguarded:
        guarded = ", ".join(_HOST_LABELS.get(host, host) for host in hosts)
        return Diagnosis(
            check="mcp-claim-guard",
            status="pass",
            detail=f"claim guard configured for {guarded}",
            remedy="",
        )
    return Diagnosis(
        check="mcp-claim-guard",
        status="warn",
        detail=(
            "Synapse MCP server without a claim guard for "
            + ", ".join(unguarded)
            + "; an MCP 'claim granted' reply is then not enforced at edit time"
        ),
        remedy="; ".join(remedies),
    )


def diagnose_mcp_hosts(
    *,
    home: Path,
    project: Path,
    env: Mapping[str, str],
    which: Callable[[str], str | None] = shutil.which,
    synapse_bin: str | None = None,
) -> list[Diagnosis]:
    """Run both host checks against the real files under ``home`` and ``project``.

    ``synapse_bin`` names this installation's executable (the ``synapse`` console
    script by default); when it cannot be resolved no definition counts as genuine.
    """
    scan = scan_host_sources(home=home, project=project, env=env)
    try:
        synapse_binary: Path | None = Path(resolve_synapse_binary(synapse_bin))
    except ValueError:
        synapse_binary = None
    classified = tuple(
        classify_definition(
            definition,
            synapse_binary=synapse_binary,
            python_executable=Path(sys.executable),
            which=which,
        )
        for definition in scan.definitions
    )
    guard_states: dict[str, tuple[str, str]] = {}
    if classified:
        report = inspect_mutation_governance(home=home, project=project)
        guard_states = {
            provider.provider: (provider.configuration_state, provider.configuration_detail)
            for provider in report.providers
        }
    return [
        check_mcp_host_sources(scan, classified),
        check_mcp_claim_guard(classified, guard_states=guard_states),
    ]
