# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — doctor diagnostics for outbound MCP execution policy
"""Focused parser, validation, and diagnosis helpers for ``doctor --mcp-config``."""

from __future__ import annotations

import argparse
from pathlib import Path

from synapse_channel.client.diagnostics import Diagnosis
from synapse_channel.core.mcp_config import McpConfigError
from synapse_channel.core.mcp_config_trust import load_trusted_mcp_config


def diagnose_mcp_config(
    config_path: str | Path,
    *,
    trust_bundle_path: str | Path | None,
    allow_repo_config: bool,
) -> Diagnosis:
    """Return the executable-policy trust posture of one outbound MCP config."""
    try:
        servers, report = load_trusted_mcp_config(
            config_path,
            trust_bundle_path=trust_bundle_path,
            allow_repo_config=allow_repo_config,
        )
    except McpConfigError as exc:
        return Diagnosis(
            check="mcp-config",
            status="fail",
            detail=str(exc),
            remedy=(
                "move config/trust outside the repository, chmod 600, configure an absolute "
                "outside-repository cwd, and repair any signature or executable-hash mismatch"
            ),
        )
    residuals: list[str] = []
    if not report.outside_repository:
        residuals.append("repository-local config override")
    if report.trust_bundle_outside_repository is False:
        residuals.append("repository-local trust bundle override")
    if not report.signed_by:
        residuals.append("unsigned manifest")
    if report.unhashed_servers:
        residuals.append(f"no executable hash for {', '.join(report.unhashed_servers)}")
    if report.repository_local_cwds:
        residuals.append(f"repository-local cwd for {', '.join(report.repository_local_cwds)}")
    if report.unbound_arguments:
        residuals.append(f"unbound command arg positions: {', '.join(report.unbound_arguments)}")
    controls = (
        f"{len(servers)} server(s), owner-only config, sealed executable snapshots, "
        f"{len(report.inherited_environment)} explicitly inherited environment variable(s)"
    )
    if residuals:
        return Diagnosis(
            check="mcp-config",
            status="warn",
            detail=f"{controls}; residual: {'; '.join(residuals)}",
            remedy=(
                "use a signed manifest, hash every native command, configure an interpreter "
                "binary as command for scripts, avoid auxiliary arguments, and avoid "
                "repository overrides"
            ),
        )
    return Diagnosis(
        check="mcp-config",
        status="pass",
        detail=f"{controls}; signed by {report.signed_by!r} with every executable hash-pinned",
    )


def validate_mcp_config_doctor_args(args: argparse.Namespace) -> str:
    """Return a parser-style error for dependent MCP doctor flags, or empty text."""
    if getattr(args, "mcp_config", None):
        return ""
    dependent = [
        flag
        for flag, present in (
            ("--mcp-config-trust-bundle", getattr(args, "mcp_config_trust_bundle", None)),
            ("--allow-repo-mcp-config", getattr(args, "allow_repo_mcp_config", False)),
        )
        if present
    ]
    if dependent:
        return f"{', '.join(dependent)} requires --mcp-config"
    return ""


def add_mcp_config_doctor_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the outbound MCP config audit flags on ``doctor``."""
    parser.add_argument(
        "--mcp-config",
        default=None,
        metavar="FILE",
        help=(
            "Audit one outbound MCP config's owner/repository/signature/executable/env trust "
            "posture (REV-SEC-08)."
        ),
    )
    parser.add_argument(
        "--mcp-config-trust-bundle",
        default=None,
        metavar="FILE",
        help="Ed25519 trust bundle required to verify --mcp-config's signed manifest.",
    )
    parser.add_argument(
        "--allow-repo-mcp-config",
        action="store_true",
        help=(
            "Audit an explicitly accepted repository-local MCP config; doctor reports the "
            "override as a warning."
        ),
    )
