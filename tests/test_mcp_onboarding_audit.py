# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP onboarding artefact audit tests

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools.audit_mcp_surface import (
    DEFAULT_DOCS,
    DEFAULT_PYPROJECT,
    DEFAULT_README,
    DEFAULT_REGISTRATION,
    DEFAULT_REGISTRY,
    DEFAULT_TEMPLATE,
    audit_docs,
    discover_surface,
    main,
    parse_args,
)


def _audit(
    *,
    registry: Path = DEFAULT_REGISTRY,
    template: Path = DEFAULT_TEMPLATE,
    pyproject: Path = DEFAULT_PYPROJECT,
    readme: Path = DEFAULT_README,
) -> tuple[str, ...]:
    return audit_docs(
        DEFAULT_REGISTRATION,
        DEFAULT_DOCS,
        registry_path=registry,
        template_path=template,
        pyproject_path=pyproject,
        readme_path=readme,
    ).errors


def test_onboarding_audit_accepts_the_live_repository_contract() -> None:
    assert _audit() == ()


def test_onboarding_audit_detects_registry_version_and_runtime_drift(tmp_path: Path) -> None:
    registry = json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    registry["version"] = "9.9.9"
    registry["packages"][0].pop("runtimeArguments")
    drifted = tmp_path / "server.json"
    drifted.write_text(json.dumps(registry), encoding="utf-8")

    errors = _audit(registry=drifted)

    assert "server.json version does not match pyproject.toml" in errors
    assert "server.json must install the optional MCP SDK through uvx --with" in errors


def test_onboarding_audit_detects_identity_and_secret_template_drift(tmp_path: Path) -> None:
    template = json.loads(DEFAULT_TEMPLATE.read_text(encoding="utf-8"))
    server = template["mcpServers"]["synapse"]
    server["command"] = "other-command"
    server["args"] = ["mcp", "--name", "OTHER/client", "PROJ/client"]
    server["env"] = {
        "SYN_IDENTITY": "PROJ/client",
        "SYNAPSE_TOKEN": "raw-secret",
    }
    drifted = tmp_path / ".mcp.json"
    drifted.write_text(json.dumps(template), encoding="utf-8")

    errors = _audit(template=drifted)

    assert "MCP template does not launch synapse mcp" in errors
    assert "MCP template does not pin one explicit matching client identity" in errors
    assert "MCP template must not embed a raw token" in errors


def test_onboarding_audit_reports_malformed_registry_and_template(tmp_path: Path) -> None:
    invalid_registry = tmp_path / "server.json"
    invalid_template = tmp_path / ".mcp.json"
    invalid_registry.write_text("{", encoding="utf-8")
    invalid_template.write_text("{", encoding="utf-8")

    assert _audit(registry=invalid_registry)[0].startswith("invalid MCP registry metadata")
    assert _audit(template=invalid_template)[0].startswith("invalid MCP client template")


def test_onboarding_audit_detects_registry_identity_and_package_shape_drift(
    tmp_path: Path,
) -> None:
    registry = json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    registry["$schema"] = "https://invalid.example/schema.json"
    registry["name"] = "invalid/name"
    registry["packages"] = []
    drifted = tmp_path / "server.json"
    drifted.write_text(json.dumps(registry), encoding="utf-8")

    errors = _audit(registry=drifted)

    assert "server.json does not use the verified MCP registry schema" in errors
    assert "server.json name must be io.github.anulum/synapse-channel" in errors
    assert "server.json must contain exactly one PyPI package" in errors


def test_onboarding_audit_detects_pypi_package_contract_drift(tmp_path: Path) -> None:
    registry = json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    package = registry["packages"][0]
    package["identifier"] = "other-package"
    package["transport"] = {"type": "sse"}
    drifted = tmp_path / "server.json"
    drifted.write_text(json.dumps(registry), encoding="utf-8")

    assert "server.json PyPI package identity/version/transport drifted" in _audit(registry=drifted)


def test_onboarding_audit_detects_console_entry_and_ownership_marker_drift(
    tmp_path: Path,
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    readme = tmp_path / "README.md"
    pyproject.write_text(
        DEFAULT_PYPROJECT.read_text(encoding="utf-8").replace(
            'synapse-channel = "synapse_channel.cli_mcp:main"',
            'synapse-channel = "synapse_channel.cli:main"',
        ),
        encoding="utf-8",
    )
    readme.write_text(
        DEFAULT_README.read_text(encoding="utf-8").replace(
            "mcp-name: io.github.anulum/synapse-channel",
            "mcp marker removed",
        ),
        encoding="utf-8",
    )

    errors = _audit(pyproject=pyproject, readme=readme)

    assert (
        "MCP registry console entry must be synapse-channel -> synapse_channel.cli_mcp:main"
        in errors
    )
    assert "README is missing the PyPI MCP ownership marker" in " ".join(errors)


def test_onboarding_audit_rejects_missing_server_and_invalid_package_metadata(
    tmp_path: Path,
) -> None:
    template = tmp_path / ".mcp.json"
    pyproject = tmp_path / "pyproject.toml"
    template.write_text('{"mcpServers": {}}', encoding="utf-8")
    pyproject.write_text("[project", encoding="utf-8")

    assert "MCP template is missing mcpServers.synapse" in _audit(template=template)
    assert _audit(pyproject=pyproject)[0].startswith("invalid MCP package metadata")


def test_onboarding_audit_cli_accepts_explicit_artifact_paths(tmp_path: Path) -> None:
    args = parse_args(
        [
            "--registry",
            str(tmp_path / "server.json"),
            "--template",
            str(tmp_path / ".mcp.json"),
            "--pyproject",
            str(tmp_path / "pyproject.toml"),
            "--readme",
            str(tmp_path / "README.md"),
        ]
    )

    assert args.registry == tmp_path / "server.json"
    assert args.template == tmp_path / ".mcp.json"
    assert args.pyproject == tmp_path / "pyproject.toml"
    assert args.readme == tmp_path / "README.md"


def test_surface_discovery_ignores_non_server_and_malformed_decorators(tmp_path: Path) -> None:
    registration = tmp_path / "registration.py"
    registration.write_text(
        """
@unrelated
async def plain(): ...

@unrelated()
async def called_plain(): ...

@factory.tool()
async def foreign_tool(): ...

@server.resource
async def uncalled_resource(): ...

@server.resource()
async def missing_uri(): ...

@server.resource(123)
async def non_string_uri(): ...

@server.tool()
async def live_tool(): ...

@server.resource("synapse://live")
async def live_resource(): ...

@server.resource("synapse://live/{item}")
async def live_template(): ...
""",
        encoding="utf-8",
    )

    surface = discover_surface(registration)

    assert surface.tools == ("live_tool",)
    assert surface.resources == ("synapse://live",)
    assert surface.resource_templates == ("synapse://live/{item}",)


def test_surface_audit_reports_tool_resource_phrase_claim_and_onboarding_drift(
    tmp_path: Path,
) -> None:
    registration = tmp_path / "registration.py"
    docs = tmp_path / "mcp.md"
    registration.write_text(
        """
@server.tool()
async def one_tool(): ...

@server.resource("synapse://one")
async def one_resource(): ...

@server.resource("synapse://one/{item}")
async def one_template(): ...
""",
        encoding="utf-8",
    )
    docs.write_text("This adapter is officially certified.", encoding="utf-8")

    result = audit_docs(registration, docs)

    assert result.ok is False
    joined = "\n".join(result.errors)
    assert "undocumented tools: one_tool" in joined
    assert "undocumented resources: synapse://one" in joined
    assert "undocumented resource templates: synapse://one/{item}" in joined
    assert "missing boundary phrases:" in joined
    assert "forbidden MCP overclaim patterns:" in joined
    assert "missing MCP onboarding tools:" in joined


def test_surface_audit_main_reports_live_success(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "MCP surface audit passed" in capsys.readouterr().out


def test_surface_audit_main_reports_artifact_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = tmp_path / "server.json"
    registry.write_text("{", encoding="utf-8")

    assert main(["--registry", str(registry)]) == 1
    assert "invalid MCP registry metadata" in capsys.readouterr().err
