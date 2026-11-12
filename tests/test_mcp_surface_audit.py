# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP surface audit CLI regressions

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from tools.audit_mcp_surface import discover_surface

from synapse_channel.mcp.bridge import SynapseHubBridge
from synapse_channel.mcp.registration import build_mcp_server

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPO_ROOT / "tools" / "audit_mcp_surface.py"
REGISTRATION = REPO_ROOT / "src" / "synapse_channel" / "mcp" / "registration.py"
DOCS = REPO_ROOT / "docs" / "mcp.md"
REGISTRY = REPO_ROOT / "server.json"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _run_audit(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AUDIT_SCRIPT), *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _assert_refusal_and_repair(flag: str, path: Path, message: str, original: str) -> None:
    """Use the real CLI to reject the changed artifact, then accept its repaired bytes."""
    refused = _run_audit("--check", flag, str(path))
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert message in refused.stderr
    assert "Traceback" not in refused.stderr
    path.write_text(original, encoding="utf-8")
    repaired = _run_audit("--check", flag, str(path))
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr


def test_mcp_surface_audit_passes_current_repository() -> None:
    guide = DOCS.read_text(encoding="utf-8")
    assert "prompt/runtime/editor behaviour" in guide
    assert "remote attach behaviour" in guide
    assert "behavior" not in guide

    result = _run_audit("--check")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "MCP surface audit passed" in result.stdout


async def test_static_audit_includes_every_tool_in_the_actual_shared_catalogue() -> None:
    """The source auditor follows the same inbox delegate as actual registration."""
    server = build_mcp_server(SynapseHubBridge(name="AUDIT/catalogue"))
    actual = {tool.name for tool in await server.list_tools()}
    assert {"synapse_inbox", "synapse_inbox_page", "synapse_owned_work"} <= actual
    assert discover_surface(REGISTRATION).tools == tuple(sorted(actual))


def test_monolithic_registration_does_not_require_an_inbox_delegate(tmp_path: Path) -> None:
    """A supplied legacy registration source still parses without an unrelated sibling."""
    registration = tmp_path / "registration.py"
    registration.write_text(
        "@server.tool()\nasync def legacy_read():\n    return 'legacy'\n", encoding="utf-8"
    )
    assert discover_surface(registration).tools == ("legacy_read",)


@pytest.mark.parametrize("tool", ["synapse_inbox", "synapse_inbox_page", "synapse_owned_work"])
def test_cli_audit_refuses_undocumented_delegated_inbox_tools(tmp_path: Path, tool: str) -> None:
    """Removing either delegated tool from the real guide must fail the public audit."""
    docs = tmp_path / "mcp.md"
    docs.write_text(
        re.sub(r"\b" + tool + r"\b", "removed_inbox_tool", DOCS.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    result = _run_audit("--check", "--docs", str(docs))
    assert result.returncode == 1
    assert f"undocumented tools: {tool}" in result.stderr


def test_mcp_surface_audit_detects_missing_documented_tool(tmp_path: Path) -> None:
    drifted_docs = tmp_path / "mcp.md"
    documented_manifest_tool = (
        "| `synapse_manifest()` | Return the capability manifest of advertised agents as JSON. |\n"
    )
    drifted_docs.write_text(
        DOCS.read_text(encoding="utf-8")
        .replace(documented_manifest_tool, "")
        .replace("`synapse_manifest`", "`manifest_removed`"),
        encoding="utf-8",
    )

    result = _run_audit("--check", "--registration", str(REGISTRATION), "--docs", str(drifted_docs))

    assert result.returncode == 1
    assert "undocumented tools: synapse_manifest" in result.stderr


def test_mcp_surface_audit_detects_missing_documented_template(tmp_path: Path) -> None:
    drifted_docs = tmp_path / "mcp.md"
    documented_template = "| `synapse://task/{task_id}` | A single board task by id. |\n"
    drifted_docs.write_text(
        DOCS.read_text(encoding="utf-8").replace(documented_template, ""),
        encoding="utf-8",
    )

    result = _run_audit("--check", "--registration", str(REGISTRATION), "--docs", str(drifted_docs))

    assert result.returncode == 1
    assert "undocumented resource templates: synapse://task/{task_id}" in result.stderr


@pytest.mark.parametrize(
    "runtime_requirement",
    ("mcp>=2.0.0", "mcp>=2.3.0", "mcp==1.30.0"),
)
def test_mcp_surface_audit_rejects_registry_runtime_constraint_drift(
    tmp_path: Path,
    runtime_requirement: str,
) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    registry["packages"][0]["runtimeArguments"][0]["value"] = runtime_requirement
    drifted_registry = tmp_path / "server.json"
    drifted_registry.write_text(json.dumps(registry), encoding="utf-8")

    result = _run_audit("--check", "--registry", str(drifted_registry))

    assert result.returncode == 1
    assert "server.json MCP runtime requirement must match pyproject.toml" in result.stderr


def test_mcp_surface_audit_accepts_semantically_equal_registry_requirement(
    tmp_path: Path,
) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    registry["packages"][0]["runtimeArguments"][0]["value"] = "MCP == 2.3.0"
    equivalent_registry = tmp_path / "server.json"
    equivalent_registry.write_text(json.dumps(registry), encoding="utf-8")

    result = _run_audit("--check", "--registry", str(equivalent_registry))

    assert result.returncode == 0, result.stderr + result.stdout
    assert "MCP surface audit passed" in result.stdout


def test_mcp_surface_audit_rejects_malformed_registry_runtime_constraint(
    tmp_path: Path,
) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    registry["packages"][0]["runtimeArguments"][0]["value"] = "mcp=>2.3.0"
    drifted_registry = tmp_path / "server.json"
    drifted_registry.write_text(json.dumps(registry), encoding="utf-8")

    result = _run_audit("--check", "--registry", str(drifted_registry))

    assert result.returncode == 1
    assert "server.json --with value must be a valid PEP 508 requirement" in result.stderr


@pytest.mark.parametrize("extra_occurrence", (0, 1, 2), ids=("dev", "mcp", "all"))
def test_mcp_surface_audit_rejects_package_extra_constraint_drift(
    tmp_path: Path,
    extra_occurrence: int,
) -> None:
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    positions = tuple(
        index for index in range(len(pyproject)) if pyproject.startswith('"mcp==2.3.0"', index)
    )
    assert len(positions) == 3
    start = positions[extra_occurrence]
    exact_requirement = '"mcp==2.3.0"'
    drifted_pyproject = tmp_path / "pyproject.toml"
    drifted_pyproject.write_text(
        pyproject[:start] + '"mcp>=2.3.0"' + pyproject[start + len(exact_requirement) :],
        encoding="utf-8",
    )

    result = _run_audit("--check", "--pyproject", str(drifted_pyproject))

    assert result.returncode == 1
    assert "extra must declare exactly mcp==2.3.0" in result.stderr


@pytest.mark.parametrize(
    ("required_phrase", "drifted_phrase"),
    (
        ("mcp==2.3.0` runtime hint", "mcp>=2.0.0` runtime hint"),
        (
            "dependency `websockets` on Python 3.11 or newer:",
            "dependency `websockets` on Python 3.10 or newer:",
        ),
        (
            "dependency `websockets` on Python 3.11 or newer:",
            "dependency `mcp` on Python 3.11 or newer:",
        ),
    ),
    ids=("optional-sdk-pin", "python-floor", "core-dependency"),
)
def test_mcp_surface_audit_rejects_documented_runtime_constraint_drift(
    tmp_path: Path,
    required_phrase: str,
    drifted_phrase: str,
) -> None:
    """The real audit CLI rejects SDK pin, Python floor and core dependency drift."""
    drifted_docs = tmp_path / "mcp.md"
    drifted_docs.write_text(
        DOCS.read_text(encoding="utf-8").replace(required_phrase, drifted_phrase),
        encoding="utf-8",
    )

    result = _run_audit("--check", "--docs", str(drifted_docs))

    assert result.returncode == 1
    assert "missing boundary phrases:" in result.stderr
    assert required_phrase in result.stderr


def test_static_source_discovery_never_executes_unrelated_decorators(tmp_path: Path) -> None:
    """Only literal server registrations count; reading a source file cannot execute it."""
    source = tmp_path / "registration.py"
    source.write_text(
        "raise RuntimeError('source must never execute')\n"
        "@passive\n@factory()\n@other.tool()\nasync def unrelated(): pass\n"
        "@server.resource()\nasync def missing_uri(): pass\n"
        "@server.resource(7)\nasync def nonliteral_uri(): pass\n"
        "@server.resource('synapse://unlisted')\nasync def visible_resource(): pass\n",
        encoding="utf-8",
    )
    surface = discover_surface(source)
    assert surface.tools == surface.resource_templates == ()
    assert surface.resources == ("synapse://unlisted",)
    result = _run_audit("--check", "--registration", str(source))
    assert result.returncode == 1
    assert "undocumented resources: synapse://unlisted" in result.stderr
    assert "missing MCP onboarding tools:" in result.stderr
    assert "source must never execute" not in result.stderr


@pytest.mark.parametrize(
    "claim",
    [
        "officially certified",
        "conformance-certified",
        "guarantees compatibility with all MCP clients",
    ],
)
def test_cli_audit_refuses_unsupported_certification_claims(tmp_path: Path, claim: str) -> None:
    original = DOCS.read_text(encoding="utf-8")
    path = tmp_path / "mcp.md"
    path.write_text(original + "\n" + claim + "\n", encoding="utf-8")
    _assert_refusal_and_repair("--docs", path, "forbidden MCP overclaim patterns:", original)


@pytest.mark.parametrize(
    ("flag", "relative", "corrupt", "message"),
    [
        ("--registry", "server.json", None, "invalid MCP registry metadata:"),
        ("--registry", "server.json", "{", "invalid MCP registry metadata:"),
        ("--template", "examples/mcp/.mcp.json", None, "invalid MCP client template:"),
        ("--template", "examples/mcp/.mcp.json", "{", "invalid MCP client template:"),
        ("--pyproject", "pyproject.toml", "[broken", "invalid MCP package metadata:"),
        (
            "--pyproject",
            "pyproject.toml",
            "[build-system]\nrequires=[]\n",
            "invalid MCP package metadata:",
        ),
        ("--readme", "README.md", None, "invalid MCP package metadata:"),
    ],
)
def test_cli_artifact_read_errors_refuse_and_recover(
    tmp_path: Path, flag: str, relative: str, corrupt: str | None, message: str
) -> None:
    original = (REPO_ROOT / relative).read_text(encoding="utf-8")
    path = tmp_path / Path(relative).name
    if corrupt is not None:
        path.write_text(corrupt, encoding="utf-8")
    _assert_refusal_and_repair(flag, path, message, original)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"$schema": "https://invalid.example/schema"},
            "does not use the verified MCP registry schema",
        ),
        ({"name": "another/project"}, "server.json name must be"),
        ({"version": "0.0.0"}, "server.json version does not match"),
        ({"packages": []}, "must contain exactly one PyPI package"),
    ],
)
def test_cli_registry_identity_drift_refuses_and_recovers(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    original = REGISTRY.read_text(encoding="utf-8")
    registry = json.loads(original)
    registry.update(changes)
    path = tmp_path / "server.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    _assert_refusal_and_repair("--registry", path, message, original)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"identifier": "another-package"}, "PyPI package identity/version/transport drifted"),
        ({"runtimeHint": "pip"}, "must install the optional MCP SDK through uvx --with"),
        ({"runtimeArguments": {}}, "must install the optional MCP SDK through uvx --with"),
        (
            {"runtimeArguments": [0, {"type": "named", "name": "--quiet"}]},
            "must install the optional MCP SDK through uvx --with",
        ),
        (
            {"runtimeArguments": [{"type": "named", "name": "--with", "value": 7}]},
            "--with value must be a valid PEP 508 requirement",
        ),
    ],
)
def test_cli_package_installation_drift_refuses_and_recovers(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    original = REGISTRY.read_text(encoding="utf-8")
    registry = json.loads(original)
    registry["packages"][0].update(changes)
    path = tmp_path / "server.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    _assert_refusal_and_repair("--registry", path, message, original)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"command": "unexpected"}, "does not launch synapse mcp"),
        ({"args": {}}, "does not launch synapse mcp"),
        ({"env": []}, "does not pin one explicit matching client identity"),
        ({"args": ["mcp"]}, "does not pin one explicit matching client identity"),
        ({"args": ["mcp", "--name"]}, "does not pin one explicit matching client identity"),
        (
            {"args": ["mcp", "--name", "different/client"]},
            "does not pin one explicit matching client identity",
        ),
        ({"env": {"SYNAPSE_TOKEN": "fixture-only-not-a-credential"}}, "must not embed a raw token"),
    ],
)
def test_cli_template_launch_drift_refuses_and_recovers(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    original = (REPO_ROOT / "examples/mcp/.mcp.json").read_text(encoding="utf-8")
    template = json.loads(original)
    template["mcpServers"]["synapse"].update(changes)
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps(template), encoding="utf-8")
    _assert_refusal_and_repair("--template", path, message, original)


def test_cli_template_requires_its_named_server(tmp_path: Path) -> None:
    original = (REPO_ROOT / "examples/mcp/.mcp.json").read_text(encoding="utf-8")
    path = tmp_path / ".mcp.json"
    path.write_text('{"mcpServers": []}', encoding="utf-8")
    _assert_refusal_and_repair("--template", path, "missing mcpServers.synapse", original)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("", "must declare optional-dependency extras"),
        ("optional-dependencies = []\n", "must declare optional-dependency extras"),
        (
            '[project.optional-dependencies]\ndev=["mcp==1.30.0"]\nall=["mcp==1.30.0"]\n',
            "must declare the mcp optional-dependency extra",
        ),
        (
            '[project.optional-dependencies]\ndev=[7,"mcp==1.30.0"]\nmcp=["mcp==1.30.0"]\nall=["mcp==1.30.0"]\n',
            "dev extra must be a valid PEP 508 requirement",
        ),
    ],
)
def test_cli_optional_sdk_profiles_refuse_and_recover(
    tmp_path: Path, replacement: str, message: str
) -> None:
    original = PYPROJECT.read_text(encoding="utf-8")
    changed, count = re.subn(
        r"^\[project\.optional-dependencies\]\n.*?(?=^\[|\Z)",
        lambda _: replacement,
        original,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert count == 1
    path = tmp_path / "pyproject.toml"
    path.write_text(changed, encoding="utf-8")
    _assert_refusal_and_repair("--pyproject", path, message, original)


def test_cli_package_requires_the_registered_console_entry(tmp_path: Path) -> None:
    original = PYPROJECT.read_text(encoding="utf-8")
    changed, count = re.subn(
        r"^\[project\.scripts\]\n.*?(?=^\[|\Z)", "", original, flags=re.MULTILINE | re.DOTALL
    )
    assert count == 1
    changed = changed.replace("[project]\n", "[project]\nscripts = []\n", 1)
    path = tmp_path / "pyproject.toml"
    path.write_text(changed, encoding="utf-8")
    _assert_refusal_and_repair("--pyproject", path, "MCP registry console entry must be", original)


def test_cli_package_requires_the_pypi_ownership_marker(tmp_path: Path) -> None:
    original = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    changed = original.replace("mcp-name: io.github.anulum/synapse-channel", "removed-marker")
    assert changed != original
    path = tmp_path / "README.md"
    path.write_text(changed, encoding="utf-8")
    _assert_refusal_and_repair(
        "--readme", path, "README is missing the PyPI MCP ownership marker", original
    )
