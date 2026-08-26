# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP surface audit CLI regressions

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_mcp_surface_audit_passes_current_repository() -> None:
    result = _run_audit("--check")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "MCP surface audit passed" in result.stdout


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
    ("mcp>=1.28.0", "mcp>=1.28.1", "mcp==2.0.0"),
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
    registry["packages"][0]["runtimeArguments"][0]["value"] = "MCP == 1.28.1"
    equivalent_registry = tmp_path / "server.json"
    equivalent_registry.write_text(json.dumps(registry), encoding="utf-8")

    result = _run_audit("--check", "--registry", str(equivalent_registry))

    assert result.returncode == 0, result.stderr + result.stdout
    assert "MCP surface audit passed" in result.stdout


def test_mcp_surface_audit_rejects_malformed_registry_runtime_constraint(
    tmp_path: Path,
) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    registry["packages"][0]["runtimeArguments"][0]["value"] = "mcp=>1.28.1"
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
        index for index in range(len(pyproject)) if pyproject.startswith('"mcp==1.28.1"', index)
    )
    assert len(positions) == 3
    start = positions[extra_occurrence]
    exact_requirement = '"mcp==1.28.1"'
    drifted_pyproject = tmp_path / "pyproject.toml"
    drifted_pyproject.write_text(
        pyproject[:start] + '"mcp>=1.28.1"' + pyproject[start + len(exact_requirement) :],
        encoding="utf-8",
    )

    result = _run_audit("--check", "--pyproject", str(drifted_pyproject))

    assert result.returncode == 1
    assert "extra must declare exactly mcp==1.28.1" in result.stderr


def test_mcp_surface_audit_rejects_documented_runtime_constraint_drift(
    tmp_path: Path,
) -> None:
    drifted_docs = tmp_path / "mcp.md"
    drifted_docs.write_text(
        DOCS.read_text(encoding="utf-8").replace(
            "mcp==1.28.1` runtime hint", "mcp>=1.28.0` runtime hint"
        ),
        encoding="utf-8",
    )

    result = _run_audit("--check", "--docs", str(drifted_docs))

    assert result.returncode == 1
    assert "missing boundary phrases:" in result.stderr
