# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dev dependency mirror drift checker regressions

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "tools" / "check_dev_dependency_drift.py"
_DRIFT_SPEC = importlib.util.spec_from_file_location("check_dev_dependency_drift", CHECKER)
assert _DRIFT_SPEC is not None
assert _DRIFT_SPEC.loader is not None
drift = importlib.util.module_from_spec(_DRIFT_SPEC)
sys.modules[_DRIFT_SPEC.name] = drift
_DRIFT_SPEC.loader.exec_module(drift)


def _run_checker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_dev_dependency_drift_passes_current_venv() -> None:
    result = _run_checker("--check", "--extra", "dev", "--extra", "benchmark")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "dev dependency mirror passed" in result.stdout
    assert "dev" in result.stdout
    assert "benchmark" in result.stdout


def test_dev_dependency_drift_detects_missing_distribution(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project.optional-dependencies]
dev = ["definitely-missing-synapse-tool>=999.0"]
""",
        encoding="utf-8",
    )

    result = _run_checker("--check", "--pyproject", str(pyproject), "--extra", "dev")

    assert result.returncode == 1
    assert "missing" in result.stderr
    assert "definitely-missing-synapse-tool" in result.stderr


def test_dev_dependency_drift_detects_stale_distribution(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project.optional-dependencies]
dev = ["pytest>=999.0"]
""",
        encoding="utf-8",
    )

    result = _run_checker("--check", "--pyproject", str(pyproject), "--extra", "dev")

    assert result.returncode == 1
    assert "stale" in result.stderr
    assert "pytest" in result.stderr


@pytest.mark.parametrize(
    ("marker", "applies"),
    [
        (f"python_version == '{sys.version_info.major}.{sys.version_info.minor}'", True),
        (f"python_version != '{sys.version_info.major}.{sys.version_info.minor}'", False),
        (f"sys_platform == '{sys.platform}'", True),
        (f"sys_platform != '{sys.platform}'", False),
        (f"os_name == '{os.name}' and extra == 'dev'", True),
        ("extra == 'docs'", False),
        ("python_version < '3.0' or extra == 'dev'", True),
    ],
)
def test_cli_checks_only_requirements_applicable_to_its_environment(
    tmp_path: Path, marker: str, applies: bool
) -> None:
    """The actual CLI enforces applicable tools and ignores foreign marker branches."""
    pyproject = tmp_path / "pyproject.toml"
    requirement = f"definitely-missing-synapse-tool>=999.0; {marker}"
    pyproject.write_text(
        "[project.optional-dependencies]\ndev = [" + json.dumps(requirement) + "]\n",
        encoding="utf-8",
    )
    result = _run_checker("--check", "--pyproject", str(pyproject), "--extra", "dev")
    assert result.returncode == (1 if applies else 0), result.stderr + result.stdout
    if applies:
        assert "missing: dev: definitely-missing-synapse-tool>=999.0" in result.stderr
        assert result.stdout == ""
    else:
        assert "0 requirement(s) across dev" in result.stdout
        assert result.stderr == ""


def test_dev_dependency_drift_docs_and_preflight_are_wired() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    install = (REPO_ROOT / "docs" / "installation.md").read_text(encoding="utf-8")
    preflight = (REPO_ROOT / "tools" / "preflight.sh").read_text(encoding="utf-8")

    assert "check_dev_dependency_drift.py --check" in readme
    assert "check_dev_dependency_drift.py --check" in install
    assert "check_dev_dependency_drift.py --check" in preflight


def test_cli_ignores_installed_metadata_without_a_distribution_name(tmp_path: Path) -> None:
    """An actual malformed metadata directory cannot break the environment inventory."""
    metadata = tmp_path / "unnamed-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nVersion: 1.0\n")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project.optional-dependencies]\ndev = ["packaging>=1"]\n')
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--pyproject", str(pyproject), "--extra", "dev"],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "1 requirement(s) across dev" in result.stdout
    assert result.stderr == ""


def test_requirement_parser_normalises_supported_lower_bounds() -> None:
    requirement = drift.parse_requirement(
        "docs", "mkdocstrings[python]>=0.26; python_version >= '3.10'"
    )

    assert requirement == drift.Requirement(
        extra="docs",
        name="mkdocstrings",
        minimum="0.26",
    )
    assert drift.parse_requirement("dev", "websockets==12.0") is None


def test_scan_requirements_reports_missing_and_stale_versions() -> None:
    requirements = (
        drift.Requirement(extra="dev", name="pytest", minimum="9.0.0"),
        drift.Requirement(extra="dev", name="ruff", minimum="999.0"),
        drift.Requirement(extra="docs", name="mkdocs-material", minimum="9.5"),
    )

    findings = drift.scan_requirements(
        requirements,
        {
            "pytest": "9.1.1",
            "ruff": "0.15.18",
        },
    )

    assert [finding.category for finding in findings] == ["stale", "missing"]
    assert findings[0].format() == "stale: dev: ruff>=999.0 (0.15.18)"
    assert findings[1].format() == "missing: docs: mkdocs-material>=9.5 (not installed)"


def test_scan_requirements_tolerates_empty_version_segments() -> None:
    findings = drift.scan_requirements(
        (drift.Requirement(extra="dev", name="ruff", minimum="0.0.1"),),
        {"ruff": "0..2"},
    )

    assert findings == ()


def test_collect_requirements_loads_selected_extras(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project.optional-dependencies]
dev = ["ruff>=0.15", "ignored==1"]
docs = ["mkdocs-material>=9.5"]
""",
        encoding="utf-8",
    )

    optional = drift.load_pyproject_extras(pyproject)

    assert drift.collect_requirements(optional, ("dev",)) == (
        drift.Requirement(extra="dev", name="ruff", minimum="0.15"),
    )


def test_load_pyproject_extras_ignores_malformed_optional_section(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
optional-dependencies = "not a table"
""",
        encoding="utf-8",
    )

    assert drift.load_pyproject_extras(pyproject) == {}


def test_installed_versions_reads_active_environment() -> None:
    installed = drift.installed_versions()

    assert "pytest" in installed


def test_main_uses_selected_extras_and_reports_success(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project.optional-dependencies]
dev = ["ruff>=0.15"]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(drift, "installed_versions", lambda: {"ruff": "0.15.18"})

    assert drift.main(["--check", "--pyproject", str(pyproject), "--extra", "dev"]) == 0
    assert "dev dependency mirror passed" in capsys.readouterr().out


def test_main_reports_findings(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project.optional-dependencies]
dev = ["ruff>=999.0"]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(drift, "installed_versions", lambda: {"ruff": "0.15.18"})

    assert drift.main(["--check", "--pyproject", str(pyproject), "--extra", "dev"]) == 1
    assert "stale: dev: ruff>=999.0" in capsys.readouterr().err
