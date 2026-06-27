# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dependency and tooling audit checker regressions

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "tools" / "audit_dependency_tooling.py"
_SPEC = importlib.util.spec_from_file_location("audit_dependency_tooling", CHECKER)
assert _SPEC is not None and _SPEC.loader is not None
audit = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = audit
_SPEC.loader.exec_module(audit)


def _run_checker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_dependency_tooling_audit_passes_current_repository() -> None:
    result = _run_checker("--check")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "dependency/tooling audit passed" in result.stdout
    assert "workflow(s)" in result.stdout


def test_preflight_audit_requires_security_and_docs_gates(tmp_path: Path) -> None:
    preflight = tmp_path / "preflight.sh"
    preflight.write_text(
        """
ruff check src tests
mypy
pytest --cov=synapse_channel
mkdocs build --strict
""",
        encoding="utf-8",
    )

    findings = audit.audit_preflight(preflight)

    assert [finding.code for finding in findings] == ["preflight-gate-missing"]
    assert "bandit" in findings[0].detail
    assert "pip-audit" in findings[0].detail


def test_preflight_audit_passes_complete_script_and_reports_missing_file(tmp_path: Path) -> None:
    preflight = tmp_path / "preflight.sh"
    preflight.write_text(
        """
ruff format --check src
ruff check src
mypy
pytest --cov=synapse_channel
bandit -q -r src tools
python -m pip_audit --skip-editable
mkdocs build --strict
python tools/check_dev_dependency_drift.py --check
python tools/audit_dependency_tooling.py --check
""",
        encoding="utf-8",
    )

    assert audit.audit_preflight(preflight) == ()
    assert audit.audit_preflight(tmp_path / "missing.sh")[0].code == "preflight-missing"


def test_action_pin_audit_detects_tag_pins_and_unpinned_uses(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        """
steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-python
  - uses: docker/login-action@650006c6eb7dba73a995cc03b0b2d7f5ca915bee
""",
        encoding="utf-8",
    )

    findings = audit.audit_workflow_action_pins(workflows)

    assert [finding.code for finding in findings] == [
        "workflow-action-not-sha-pinned",
        "workflow-action-unpinned",
    ]
    assert "actions/checkout@v4" in findings[0].detail
    assert "actions/setup-python" in findings[1].detail


def test_action_pin_audit_passes_sha_pins_and_skips_local_actions(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        """
steps:
  - uses: ./local/action
  - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0
""",
        encoding="utf-8",
    )

    assert audit.audit_workflow_action_pins(workflows) == ()
    assert audit.audit_workflow_action_pins(tmp_path / "missing")[0].code == "workflow-dir-missing"


def test_dependabot_audit_requires_actions_pip_and_docker(tmp_path: Path) -> None:
    config = tmp_path / "dependabot.yml"
    config.write_text(
        """
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
""",
        encoding="utf-8",
    )

    findings = audit.audit_dependabot(config)

    assert [finding.code for finding in findings] == ["dependabot-ecosystem-missing"]
    assert "docker" in findings[0].detail
    assert "pip" in findings[0].detail


def test_dependabot_audit_passes_required_ecosystems_and_reports_missing_file(
    tmp_path: Path,
) -> None:
    config = tmp_path / "dependabot.yml"
    config.write_text(
        """
version: 2
updates:
  - package-ecosystem: github-actions
  - package-ecosystem: pip
  - package-ecosystem: docker
""",
        encoding="utf-8",
    )

    assert audit.audit_dependabot(config) == ()
    assert audit.audit_dependabot(tmp_path / "missing.yml")[0].code == "dependabot-missing"


def test_pypi_metadata_audit_requires_publish_and_download_surfaces(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "synapse-channel"
version = "1.2.3"
description = "x"
readme = "README.md"
license = "AGPL-3.0-or-later"

[project.urls]
Repository = "https://example.invalid/repo"
""",
        encoding="utf-8",
    )
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "publish.yml").write_text("permissions:\n  contents: read\n", encoding="utf-8")

    findings = audit.audit_pypi_metadata(
        pyproject=pyproject,
        publish_workflow=workflows / "publish.yml",
        downloads_workflow=workflows / "pypi-downloads.yml",
        downloads_tool=tmp_path / "tools" / "pypi_downloads.py",
    )

    assert [finding.code for finding in findings] == [
        "pypi-url-missing",
        "pypi-trusted-publish-missing",
        "pypi-download-tracker-missing",
        "pypi-download-tool-missing",
    ]
    assert "Documentation" in findings[0].detail
    assert "id-token: write" in findings[1].detail


def test_pypi_metadata_audit_passes_complete_surfaces_and_handles_missing_project(
    tmp_path: Path,
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "synapse-channel"
version = "1.2.3"
description = "Local coordination"
readme = "README.md"
license = "AGPL-3.0-or-later"

[project.urls]
Repository = "https://example.invalid/repo"
Documentation = "https://example.invalid/docs"
Changelog = "https://example.invalid/changelog"
Issues = "https://example.invalid/issues"
""",
        encoding="utf-8",
    )
    publish = tmp_path / "publish.yml"
    publish.write_text(
        "permissions:\n  id-token: write\nsteps:\n  - uses: pypa/gh-action-pypi-publish@abc\n",
        encoding="utf-8",
    )
    downloads = tmp_path / "pypi-downloads.yml"
    downloads.write_text("run: python tools/pypi_downloads.py\n", encoding="utf-8")
    downloads_tool = tmp_path / "tools" / "pypi_downloads.py"
    downloads_tool.parent.mkdir()
    downloads_tool.write_text("print('ok')\n", encoding="utf-8")

    assert (
        audit.audit_pypi_metadata(
            pyproject=pyproject,
            publish_workflow=publish,
            downloads_workflow=downloads,
            downloads_tool=downloads_tool,
        )
        == ()
    )
    missing_project = tmp_path / "missing-pyproject.toml"
    assert audit._load_project_metadata(missing_project) == {}


def test_main_reports_findings_for_custom_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    preflight = tmp_path / "preflight.sh"
    preflight.write_text("ruff check src\n", encoding="utf-8")
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "ci.yml").write_text("steps:\n  - uses: actions/checkout@v4\n", encoding="utf-8")
    dependabot = tmp_path / "dependabot.yml"
    dependabot.write_text("version: 2\nupdates: []\n", encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'x'\nversion = '0'\n", encoding="utf-8")

    code = audit.main(
        [
            "--check",
            "--preflight",
            str(preflight),
            "--workflows",
            str(workflows),
            "--dependabot",
            str(dependabot),
            "--pyproject",
            str(pyproject),
            "--publish-workflow",
            str(tmp_path / "publish.yml"),
            "--downloads-workflow",
            str(tmp_path / "pypi-downloads.yml"),
            "--downloads-tool",
            str(tmp_path / "pypi_downloads.py"),
        ]
    )

    assert code == 1
    captured = capsys.readouterr()
    assert "preflight-gate-missing" in captured.err
    assert "workflow-action-not-sha-pinned" in captured.err


def test_main_reports_success_for_complete_custom_repo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    preflight = tmp_path / "preflight.sh"
    preflight.write_text(
        """
ruff format --check src
ruff check src
mypy
pytest --cov=synapse_channel
bandit -q -r src tools
python -m pip_audit --skip-editable
mkdocs build --strict
python tools/check_dev_dependency_drift.py --check
python tools/audit_dependency_tooling.py --check
""",
        encoding="utf-8",
    )
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "ci.yml").write_text(
        "steps:\n  - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0\n",
        encoding="utf-8",
    )
    dependabot = tmp_path / "dependabot.yml"
    dependabot.write_text(
        """
version: 2
updates:
  - package-ecosystem: github-actions
  - package-ecosystem: pip
  - package-ecosystem: docker
""",
        encoding="utf-8",
    )
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "synapse-channel"
version = "1.2.3"
description = "Local coordination"
readme = "README.md"
license = "AGPL-3.0-or-later"

[project.urls]
Repository = "https://example.invalid/repo"
Documentation = "https://example.invalid/docs"
Changelog = "https://example.invalid/changelog"
Issues = "https://example.invalid/issues"
""",
        encoding="utf-8",
    )
    publish = tmp_path / "publish.yml"
    publish.write_text(
        "permissions:\n  id-token: write\nsteps:\n  - uses: pypa/gh-action-pypi-publish@abc\n",
        encoding="utf-8",
    )
    downloads = tmp_path / "pypi-downloads.yml"
    downloads.write_text("run: python tools/pypi_downloads.py\n", encoding="utf-8")
    downloads_tool = tmp_path / "pypi_downloads.py"
    downloads_tool.write_text("print('ok')\n", encoding="utf-8")

    code = audit.main(
        [
            "--check",
            "--preflight",
            str(preflight),
            "--workflows",
            str(workflows),
            "--dependabot",
            str(dependabot),
            "--pyproject",
            str(pyproject),
            "--publish-workflow",
            str(publish),
            "--downloads-workflow",
            str(downloads),
            "--downloads-tool",
            str(downloads_tool),
        ]
    )

    assert code == 0
    assert "dependency/tooling audit passed: 1 workflow(s) scanned" in capsys.readouterr().out
