# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP surface audit CLI regressions

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPO_ROOT / "tools" / "audit_mcp_surface.py"
REGISTRATION = REPO_ROOT / "src" / "synapse_channel" / "mcp" / "registration.py"
DOCS = REPO_ROOT / "docs" / "mcp.md"


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
        DOCS.read_text(encoding="utf-8").replace(documented_manifest_tool, ""),
        encoding="utf-8",
    )

    result = _run_audit("--check", "--registration", str(REGISTRATION), "--docs", str(drifted_docs))

    assert result.returncode == 1
    assert "undocumented tools: synapse_manifest" in result.stderr
