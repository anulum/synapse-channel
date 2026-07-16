# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provenance orchestration for outbound MCP execution policy
"""Load outbound MCP policy only after its independent trust gates pass.

This orchestrator binds the strict schema, owner-only file floor, active-repo
boundary, optional manifest signature, and launch verifier. Signature mechanics
live in :mod:`synapse_channel.core.mcp_config_signing`; executable, working-
directory, and environment mechanics live in
:mod:`synapse_channel.core.mcp_config_launch`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.mcp_config import McpConfigError, McpServerSpec, parse_mcp_config
from synapse_channel.core.mcp_config_launch import (
    McpExecutableEvidence,
    validate_mcp_server_launch,
)
from synapse_channel.core.mcp_config_signing import verify_mcp_config_signature
from synapse_channel.core.secret_files import SecretFileError, read_secret_file


@dataclass(frozen=True)
class McpConfigTrustReport:
    """Evidence returned after a complete outbound MCP config trust check.

    Parameters
    ----------
    config_path : str
        Absolute display path of the owner-only config.
    repository_root : str
        Active repository root used for the outside-repository boundary, or
        empty when no repository was discovered.
    outside_repository : bool
        Whether the config is outside that root.
    signed_by : str
        Verified Ed25519 key id, or empty for an accepted unsigned config.
    executables : tuple[McpExecutableEvidence, ...]
        Same-descriptor executable observations in server-name order.
    inherited_environment : tuple[str, ...]
        ``server:NAME`` entries explicitly approved for parent inheritance.
    repository_local_cwds : tuple[str, ...]
        Servers whose explicit cwd uses the repository-local escape hatch.
    unbound_arguments : tuple[str, ...]
        ``server:index:value`` command arguments not covered by the executable snapshot.
    """

    config_path: str
    repository_root: str
    outside_repository: bool
    signed_by: str
    executables: tuple[McpExecutableEvidence, ...]
    inherited_environment: tuple[str, ...]
    repository_local_cwds: tuple[str, ...]
    unbound_arguments: tuple[str, ...]

    @property
    def unhashed_servers(self) -> tuple[str, ...]:
        """Return server names protected only by absolute-path pins."""
        return tuple(item.server for item in self.executables if not item.hash_pinned)


def load_trusted_mcp_config(
    path: str | Path,
    *,
    trust_bundle_path: str | Path | None = None,
    allow_repo_config: bool = False,
    repository_root: str | Path | None = None,
) -> tuple[dict[str, McpServerSpec], McpConfigTrustReport]:
    """Load and verify one outbound MCP execution-policy document.

    The default accepts an unsigned document only when its file is owner-only,
    no-follow, and outside the active repository. Supplying ``trust_bundle_path``
    additionally requires and verifies the document's Ed25519 signature. The
    trust bundle is subject to the same owner/outside-repository floor.

    Parameters
    ----------
    path : str or pathlib.Path
        Outbound MCP JSON config.
    trust_bundle_path : str or pathlib.Path or None, optional
        Owner-controlled trust bundle for a required manifest signature.
    allow_repo_config : bool, optional
        Explicit compatibility escape hatch allowing config and trust material
        inside the active repository. The owner-only floor still applies.
    repository_root : str or pathlib.Path or None, optional
        Repository boundary to enforce. ``None`` discovers the nearest ``.git``
        ancestor of the current working directory.

    Returns
    -------
    tuple[dict[str, McpServerSpec], McpConfigTrustReport]
        Parsed server policy plus observed trust evidence.

    Raises
    ------
    McpConfigError
        If file provenance, JSON, signature, schema, executable, or working
        directory validation fails.
    """
    config_path = Path(path).expanduser()
    root = _repository_root(repository_root)
    outside_repository = _outside_repository(config_path, root)
    if not outside_repository and not allow_repo_config:
        raise McpConfigError(
            f"MCP config {config_path.absolute()} is inside the active repository {root}; "
            "move it to an owner-controlled config directory or pass --allow-repo-mcp-config"
        )
    document = _read_owner_json(config_path, label="--config")
    signed_by = ""
    if trust_bundle_path is not None:
        bundle_path = Path(trust_bundle_path).expanduser()
        if not _outside_repository(bundle_path, root) and not allow_repo_config:
            raise McpConfigError(
                f"MCP config trust bundle {bundle_path.absolute()} is inside the active repository "
                f"{root}; move it to an owner-controlled config directory"
            )
        trust_document = _read_owner_json(bundle_path, label="--config-trust-bundle")
        signed_by = verify_mcp_config_signature(document, trust_document)
    elif "signature" in document:
        raise McpConfigError(
            "MCP config carries a signature but no --config-trust-bundle was supplied"
        )
    servers = parse_mcp_config(document)
    repository_local_cwds: list[str] = []
    for server in servers.values():
        if not server.cwd:
            raise McpConfigError(
                f"MCP server {server.name!r}: cwd is required so the child never inherits "
                "the caller's repository working directory"
            )
        if not _outside_repository(Path(server.cwd), root):
            if not allow_repo_config:
                raise McpConfigError(
                    f"MCP server {server.name!r}: cwd {server.cwd} is inside the active "
                    "repository; configure an outside-repository directory"
                )
            repository_local_cwds.append(server.name)
    executable_evidence = tuple(
        validate_mcp_server_launch(servers[name]) for name in sorted(servers)
    )
    inherited = tuple(
        f"{server.name}:{name}"
        for server in (servers[name] for name in sorted(servers))
        for name in server.inherit_env
    )
    unbound_arguments = tuple(
        f"{server.name}:{index}:{argument}"
        for server in (servers[name] for name in sorted(servers))
        for index, argument in enumerate(server.args)
    )
    return servers, McpConfigTrustReport(
        config_path=str(config_path.absolute()),
        repository_root="" if root is None else str(root),
        outside_repository=outside_repository,
        signed_by=signed_by,
        executables=executable_evidence,
        inherited_environment=inherited,
        repository_local_cwds=tuple(sorted(repository_local_cwds)),
        unbound_arguments=unbound_arguments,
    )


def discover_repository_root(start: str | Path) -> Path | None:
    """Return the nearest lexical ``.git`` ancestor of ``start``."""
    candidate = Path(start).expanduser().absolute()
    if not candidate.is_dir():
        candidate = candidate.parent
    for directory in (candidate, *candidate.parents):
        if (directory / ".git").exists():
            return directory.resolve()
    return None


def _read_owner_json(path: Path, *, label: str) -> dict[str, Any]:
    """Read duplicate-rejecting JSON through the shared owner-only file floor."""
    try:
        text = read_secret_file(path, flag=label, require_single_link=True)
    except SecretFileError as exc:
        raise McpConfigError(str(exc)) from exc

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise McpConfigError(f"{label}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        decoded = json.loads(text, object_pairs_hook=reject_duplicates)
    except json.JSONDecodeError as exc:
        raise McpConfigError(f"{label}: invalid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise McpConfigError(f"{label}: document must be a JSON object")
    return decoded


def _repository_root(explicit: str | Path | None) -> Path | None:
    """Resolve an explicit root or discover one from the current directory."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    return discover_repository_root(Path.cwd())


def _outside_repository(path: Path, root: Path | None) -> bool:
    """Return whether ``path`` is outside ``root``; no root means no repo boundary."""
    if root is None:
        return True
    try:
        path.resolve(strict=False).relative_to(root)
    except ValueError:
        return True
    return False
