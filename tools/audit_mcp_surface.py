#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — audit the documented MCP surface against registration code
"""Verify that the documented MCP surface matches the registered server.

The MCP adapter is intentionally optional and isolated from the hub. This checker
keeps the public MCP documentation aligned with the real FastMCP registration
module by parsing the registered tool functions and resource URIs from source and
confirming that each one appears in ``docs/mcp.md``. It also checks the boundary
language that prevents accidental overclaiming: MCP remains an adapter process,
hub authentication still uses the normal Synapse token path, and the MCP SDK is
not a core dependency.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRATION = REPO_ROOT / "src" / "synapse_channel" / "mcp" / "registration.py"
DEFAULT_DOCS = REPO_ROOT / "docs" / "mcp.md"
DEFAULT_REGISTRY = REPO_ROOT / "server.json"
DEFAULT_TEMPLATE = REPO_ROOT / "examples" / "mcp" / ".mcp.json"
DEFAULT_PYPROJECT = REPO_ROOT / "pyproject.toml"
DEFAULT_README = REPO_ROOT / "README.md"

REGISTRY_NAME = "io.github.anulum/synapse-channel"
REGISTRY_SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"

REQUIRED_ONBOARDING_TOOLS = frozenset(
    {"synapse_send", "synapse_inbox", "synapse_board", "synapse_claim", "synapse_status"}
)
"""Minimum send/read/coordinate/status loop promised by MCP-first onboarding."""

REQUIRED_DOC_PHRASES = (
    "`synapse mcp` runs an MCP server over stdio",
    "hub itself never learns about MCP",
    "separate adapter process, not a hub change",
    "The MCP SDK is an **optional extra**",
    "core install keeps its single",
    "--token-file",
    "SYNAPSE_TOKEN",
    "adapter registers on the\nhub under `--name`",
    "`synapse_inbox",
    "`synapse_status",
    "no MCP `synapse_lock(command)` tool",
    "the vendor `claude/channel` extension",
    "examples/mcp/.mcp.json",
)
"""Boundary phrases that must stay present in the MCP guide."""

FORBIDDEN_CLAIM_PATTERNS = (
    re.compile(r"\bofficial(?:ly)?\s+certified\b", re.IGNORECASE),
    re.compile(r"\bconformance-certified\b", re.IGNORECASE),
    re.compile(r"\bguarantees?\s+compatibility\s+with\s+all\s+MCP\s+clients\b", re.IGNORECASE),
)
"""Public overclaim patterns that the MCP guide must not use."""


@dataclass(frozen=True)
class McpSurface:
    """Registered MCP tools and resource URIs discovered from source."""

    tools: tuple[str, ...]
    resources: tuple[str, ...]
    resource_templates: tuple[str, ...]


@dataclass(frozen=True)
class AuditResult:
    """Structured outcome of one MCP surface audit."""

    tools: tuple[str, ...]
    resources: tuple[str, ...]
    resource_templates: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Return ``True`` when the audit found no drift."""
        return not self.errors


@dataclass(frozen=True)
class CliArgs:
    """Parsed command-line arguments for the MCP surface audit."""

    registration: Path
    docs: Path
    registry: Path
    template: Path
    pyproject: Path
    readme: Path
    check: bool


def _is_server_decorator(decorator: ast.expr, name: str) -> bool:
    """Return whether ``decorator`` is a call to ``server.<name>()``."""
    if not isinstance(decorator, ast.Call):
        return False
    if not isinstance(decorator.func, ast.Attribute):
        return False
    if decorator.func.attr != name:
        return False
    return isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == "server"


def _resource_uri(decorator: ast.expr) -> str | None:
    """Return a resource URI from ``@server.resource("...")`` when present."""
    if not _is_server_decorator(decorator, "resource"):
        return None
    call = decorator
    if not isinstance(call, ast.Call) or not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def discover_surface(registration_path: Path) -> McpSurface:
    """Parse the MCP registration module and return registered surface names."""
    tree = ast.parse(registration_path.read_text(encoding="utf-8"), filename=str(registration_path))
    tools: list[str] = []
    resources: list[str] = []
    resource_templates: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if _is_server_decorator(decorator, "tool"):
                tools.append(node.name)
            resource = _resource_uri(decorator)
            if resource is not None:
                if "{" in resource and "}" in resource:
                    resource_templates.append(resource)
                else:
                    resources.append(resource)

    return McpSurface(
        tools=tuple(sorted(tools)),
        resources=tuple(sorted(resources)),
        resource_templates=tuple(sorted(resource_templates)),
    )


def audit_docs(
    registration_path: Path,
    docs_path: Path,
    *,
    registry_path: Path = DEFAULT_REGISTRY,
    template_path: Path = DEFAULT_TEMPLATE,
    pyproject_path: Path = DEFAULT_PYPROJECT,
    readme_path: Path = DEFAULT_README,
) -> AuditResult:
    """Compare registered MCP tools/resources with the public MCP guide."""
    surface = discover_surface(registration_path)
    docs = docs_path.read_text(encoding="utf-8")
    errors: list[str] = []

    missing_tools = tuple(tool for tool in surface.tools if f"`{tool}" not in docs)
    if missing_tools:
        errors.append(f"undocumented tools: {', '.join(missing_tools)}")

    missing_resources = tuple(
        resource for resource in surface.resources if f"`{resource}`" not in docs
    )
    if missing_resources:
        errors.append(f"undocumented resources: {', '.join(missing_resources)}")

    missing_templates = tuple(
        template for template in surface.resource_templates if f"`{template}`" not in docs
    )
    if missing_templates:
        errors.append(f"undocumented resource templates: {', '.join(missing_templates)}")

    missing_phrases = tuple(phrase for phrase in REQUIRED_DOC_PHRASES if phrase not in docs)
    if missing_phrases:
        errors.append(f"missing boundary phrases: {'; '.join(missing_phrases)}")

    forbidden_claims = tuple(
        pattern.pattern for pattern in FORBIDDEN_CLAIM_PATTERNS if pattern.search(docs)
    )
    if forbidden_claims:
        errors.append(f"forbidden MCP overclaim patterns: {'; '.join(forbidden_claims)}")

    missing_onboarding = tuple(sorted(REQUIRED_ONBOARDING_TOOLS.difference(surface.tools)))
    if missing_onboarding:
        errors.append(f"missing MCP onboarding tools: {', '.join(missing_onboarding)}")

    errors.extend(
        _audit_onboarding_artifacts(
            registry_path=registry_path,
            template_path=template_path,
            pyproject_path=pyproject_path,
            readme_path=readme_path,
        )
    )

    return AuditResult(
        tools=surface.tools,
        resources=surface.resources,
        resource_templates=surface.resource_templates,
        errors=tuple(errors),
    )


def _audit_onboarding_artifacts(
    *,
    registry_path: Path,
    template_path: Path,
    pyproject_path: Path,
    readme_path: Path,
) -> list[str]:
    """Return registry, package-entry, ownership-marker, and template drift."""
    errors: list[str] = []
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid MCP registry metadata: {exc}"]
    try:
        template = json.loads(template_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid MCP client template: {exc}"]
    try:
        project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]
        readme = readme_path.read_text(encoding="utf-8")
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        return [f"invalid MCP package metadata: {exc}"]

    version = str(project.get("version", ""))
    scripts = project.get("scripts", {})
    if (
        not isinstance(scripts, dict)
        or scripts.get("synapse-channel") != "synapse_channel.cli_mcp:main"
    ):
        errors.append(
            "MCP registry console entry must be synapse-channel -> synapse_channel.cli_mcp:main"
        )
    if f"mcp-name: {REGISTRY_NAME}" not in readme:
        errors.append(f"README is missing the PyPI MCP ownership marker for {REGISTRY_NAME}")

    packages = registry.get("packages")
    package = packages[0] if isinstance(packages, list) and len(packages) == 1 else None
    if registry.get("$schema") != REGISTRY_SCHEMA:
        errors.append("server.json does not use the verified MCP registry schema")
    if registry.get("name") != REGISTRY_NAME:
        errors.append(f"server.json name must be {REGISTRY_NAME}")
    if registry.get("version") != version:
        errors.append("server.json version does not match pyproject.toml")
    if not isinstance(package, dict):
        errors.append("server.json must contain exactly one PyPI package")
    else:
        if (
            package.get("registryType") != "pypi"
            or package.get("identifier") != "synapse-channel"
            or package.get("version") != version
            or package.get("transport") != {"type": "stdio"}
        ):
            errors.append("server.json PyPI package identity/version/transport drifted")
        runtime_args = package.get("runtimeArguments")
        if package.get("runtimeHint") != "uvx" or not _has_mcp_runtime(runtime_args):
            errors.append("server.json must install the optional MCP SDK through uvx --with")

    servers = template.get("mcpServers")
    synapse = servers.get("synapse") if isinstance(servers, dict) else None
    if not isinstance(synapse, dict):
        errors.append("MCP template is missing mcpServers.synapse")
    else:
        args = synapse.get("args")
        env = synapse.get("env")
        arguments = args if isinstance(args, list) else []
        environment = env if isinstance(env, dict) else {}
        if (
            synapse.get("command") != "synapse"
            or not isinstance(args, list)
            or "mcp" not in arguments
        ):
            errors.append("MCP template does not launch synapse mcp")
        identity = environment.get("SYN_IDENTITY")
        name_index = arguments.index("--name") if "--name" in arguments else -1
        if (
            not isinstance(identity, str)
            or name_index < 0
            or name_index + 1 >= len(arguments)
            or arguments[name_index + 1] != identity
        ):
            errors.append("MCP template does not pin one explicit matching client identity")
        serialized = json.dumps(template).lower()
        if "synapse_token" in serialized or '"--token"' in serialized:
            errors.append("MCP template must not embed a raw token")
    return errors


def _has_mcp_runtime(value: object) -> bool:
    """Return whether registry runtime args request the MCP SDK through ``--with``."""
    if not isinstance(value, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("type") == "named"
        and item.get("name") == "--with"
        and str(item.get("value", "")).startswith("mcp>=")
        for item in value
    )


def parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    """Parse CLI arguments for the MCP surface audit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registration",
        type=Path,
        default=DEFAULT_REGISTRATION,
        help="Path to src/synapse_channel/mcp/registration.py",
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--pyproject", type=Path, default=DEFAULT_PYPROJECT)
    parser.add_argument("--readme", type=Path, default=DEFAULT_README)
    parser.add_argument(
        "--docs",
        type=Path,
        default=DEFAULT_DOCS,
        help="Path to docs/mcp.md",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check-only mode; kept for consistency with other repository guards.",
    )
    namespace = parser.parse_args(argv)
    return CliArgs(
        registration=namespace.registration,
        docs=namespace.docs,
        registry=namespace.registry,
        template=namespace.template,
        pyproject=namespace.pyproject,
        readme=namespace.readme,
        check=namespace.check,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the MCP surface audit and return a process exit code."""
    args = parse_args(argv)
    _ = args.check
    result = audit_docs(
        args.registration,
        args.docs,
        registry_path=args.registry,
        template_path=args.template,
        pyproject_path=args.pyproject,
        readme_path=args.readme,
    )
    if not result.ok:
        for error in result.errors:
            print(error, file=sys.stderr)
        return 1

    print(
        "MCP surface audit passed: "
        f"{len(result.tools)} tools, {len(result.resources)} resources, "
        f"{len(result.resource_templates)} resource templates documented"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
