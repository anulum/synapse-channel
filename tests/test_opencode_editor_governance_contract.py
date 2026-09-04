# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — OpenCode editor governance environment contract
"""Verify deterministic imports for isolated real-editor child processes."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from e2e.opencode_editors.governance_contract import source_environment
from fixtures.opencode.process import governed_project_environment, isolated_environment


def test_source_environment_uses_absolute_checkout_roots() -> None:
    """Replace inherited paths with the source and fixture roots in this checkout."""
    environment = {
        "PYTHONPATH": "relative-injection",
        "FORCE_COLOR": "1",
        "RETAINED": "value",
    }

    result = source_environment(environment)

    roots = tuple(Path(entry) for entry in result["PYTHONPATH"].split(os.pathsep))
    repository = Path(__file__).resolve().parents[1]
    assert roots == (repository / "src", repository / "tests")
    assert all(root.is_absolute() for root in roots)
    assert "FORCE_COLOR" not in result
    assert result["RETAINED"] == "value"
    assert result is environment


def test_isolated_environment_disables_network_bootstrap(tmp_path: Path) -> None:
    """Keep a fresh real-runtime home independent of the package registry."""
    home = tmp_path / "home"
    home.mkdir()

    environment = isolated_environment(
        home,
        "http://127.0.0.1:12345/v1",
        pure=False,
        disable_project_config=True,
    )

    config_dir = home / ".config" / "opencode"
    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o500
    assert environment["OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER"] == "1"
    assert environment["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"


def test_governed_project_environment_loads_installed_adapter(tmp_path: Path) -> None:
    """Use exact installer output while project discovery remains disabled."""
    project = tmp_path / "project"
    plugin = project / ".opencode" / "plugins" / "synapse-claim-guard.js"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("export const SynapseClaimGuard = async () => ({});\n", encoding="utf-8")
    installed = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {"synapse": {"type": "local", "command": ["synapse", "mcp-serve"]}},
        "permission": {"write": "allow"},
    }
    (project / ".opencode" / "opencode.json").write_text(
        json.dumps(installed) + "\n",
        encoding="utf-8",
    )
    environment = {
        "OPENCODE_CONFIG_CONTENT": json.dumps({"provider": {"test": {"name": "test provider"}}})
    }

    result = governed_project_environment(environment, project)

    configured = json.loads(result["OPENCODE_CONFIG_CONTENT"])
    assert configured["provider"] == {"test": {"name": "test provider"}}
    assert configured["mcp"] == installed["mcp"]
    assert configured["permission"] == installed["permission"]
    assert configured["plugin"] == [plugin.resolve().as_uri()]
    assert result["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    assert "OPENCODE_DISABLE_PROJECT_CONFIG" not in environment


def test_governed_project_environment_rejects_missing_plugin(tmp_path: Path) -> None:
    """Fail closed when the adapter installer did not finish its output."""
    config = tmp_path / ".opencode" / "opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="claim-guard plugin is missing"):
        governed_project_environment(
            {"OPENCODE_CONFIG_CONTENT": "{}"},
            tmp_path,
        )
