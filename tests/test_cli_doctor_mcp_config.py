# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — focused doctor tests for outbound MCP execution policy

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import synapse_channel.cli_doctor_mcp_config as doctor_mcp
from _platform_caps import requires_sealed_launch
from _portable_exec import install_posix_tool
from synapse_channel.cli_doctor_mcp_config import (
    add_mcp_config_doctor_arguments,
    diagnose_mcp_config,
    validate_mcp_config_doctor_args,
)
from synapse_channel.core.mcp_config import McpConfigError


def test_mcp_doctor_dependent_flags_require_config() -> None:
    assert (
        validate_mcp_config_doctor_args(
            argparse.Namespace(
                mcp_config=None,
                mcp_config_trust_bundle="trust.json",
                allow_repo_mcp_config=True,
            )
        )
        == "--mcp-config-trust-bundle, --allow-repo-mcp-config requires --mcp-config"
    )
    assert (
        validate_mcp_config_doctor_args(
            argparse.Namespace(
                mcp_config=Path("config.json"),
                mcp_config_trust_bundle="trust.json",
                allow_repo_mcp_config=True,
            )
        )
        == ""
    )
    assert validate_mcp_config_doctor_args(argparse.Namespace()) == ""


def test_mcp_doctor_argument_registration_is_focused() -> None:
    parser = argparse.ArgumentParser()
    add_mcp_config_doctor_arguments(parser)
    args = parser.parse_args(
        [
            "--mcp-config",
            "config.json",
            "--mcp-config-trust-bundle",
            "trust.json",
            "--allow-repo-mcp-config",
        ]
    )
    assert args.mcp_config == "config.json"
    assert args.mcp_config_trust_bundle == "trust.json"
    assert args.allow_repo_mcp_config is True


def test_mcp_doctor_reports_platform_boundary_as_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise McpConfigError("secure executable validation is unavailable on this platform")

    monkeypatch.setattr(doctor_mcp, "load_trusted_mcp_config", unavailable)
    result = diagnose_mcp_config(
        "config.json",
        trust_bundle_path=None,
        allow_repo_config=False,
    )
    assert result.status == "fail"
    assert "unavailable on this platform" in result.detail


def test_mcp_doctor_reports_every_residual_as_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = SimpleNamespace(
        outside_repository=False,
        trust_bundle_outside_repository=False,
        signed_by=None,
        unhashed_servers=("plain",),
        repository_local_cwds=("local",),
        unbound_arguments=("local:0",),
        inherited_environment=("LANG",),
    )
    monkeypatch.setattr(
        doctor_mcp,
        "load_trusted_mcp_config",
        lambda *_args, **_kwargs: ((object(), object()), report),
    )

    result = diagnose_mcp_config(
        "config.json",
        trust_bundle_path=None,
        allow_repo_config=True,
    )

    assert result.status == "warn"
    assert "repository-local config override" in result.detail
    assert "repository-local trust bundle override" in result.detail
    assert "unsigned manifest" in result.detail
    assert "no executable hash for plain" in result.detail
    assert "repository-local cwd for local" in result.detail
    assert "unbound command arg positions: local:0" in result.detail


def test_mcp_doctor_passes_fully_pinned_signed_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = SimpleNamespace(
        outside_repository=True,
        trust_bundle_outside_repository=True,
        signed_by="ops",
        unhashed_servers=(),
        repository_local_cwds=(),
        unbound_arguments=(),
        inherited_environment=(),
    )
    monkeypatch.setattr(
        doctor_mcp,
        "load_trusted_mcp_config",
        lambda *_args, **_kwargs: ((object(),), report),
    )

    result = diagnose_mcp_config(
        "config.json",
        trust_bundle_path="trust.json",
        allow_repo_config=False,
    )

    assert result.status == "pass"
    assert "signed by 'ops'" in result.detail


@requires_sealed_launch
def test_mcp_doctor_never_reflects_unbound_argument_values(tmp_path: Path) -> None:
    executable = tmp_path / "mcp-server"
    install_posix_tool(executable)
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "echo",
                        "command": str(executable),
                        "args": ["--api-key=TOP-SECRET"],
                        "cwd": str(tmp_path),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config.chmod(0o600)

    result = diagnose_mcp_config(
        config,
        trust_bundle_path=None,
        allow_repo_config=False,
    )

    assert result.status == "warn"
    assert "unbound command arg positions: echo:0" in result.detail
    assert "TOP-SECRET" not in result.detail


def test_mcp_doctor_sanitizes_an_integer_beyond_the_json_decoder_limit(tmp_path: Path) -> None:
    config = tmp_path / "decoder-limit-timeout.json"
    config.write_text(
        '{"servers":[{"name":"echo","command":"/bin/true","cwd":"/tmp",'
        '"timeout_seconds":' + "9" * 5001 + "}]}",
        encoding="utf-8",
    )
    config.chmod(0o600)

    result = diagnose_mcp_config(
        config,
        trust_bundle_path=None,
        allow_repo_config=False,
    )

    assert result.status == "fail"
    assert "invalid JSON numeric value" in result.detail
    assert "ValueError" not in result.detail
    assert "Exceeds the limit" not in result.detail
