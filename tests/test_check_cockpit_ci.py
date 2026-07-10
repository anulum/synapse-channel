# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cockpit CI and npm-lock guard regressions
"""Tests for the repository's cockpit CI contract guard."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "tools" / "check_cockpit_ci.py"
_SPEC = importlib.util.spec_from_file_location("check_cockpit_ci", CHECKER)
assert _SPEC is not None and _SPEC.loader is not None
guard = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = guard
_SPEC.loader.exec_module(guard)


def _package() -> dict[str, object]:
    return {
        "name": "cockpit",
        "version": "1.0.0",
        "license": "AGPL-3.0-or-later",
        "scripts": {name: name for name in guard.REQUIRED_SCRIPTS},
        "dependencies": {"react": "1"},
        "devDependencies": {"@playwright/test": "1"},
    }


def _lock(package: dict[str, object]) -> dict[str, object]:
    root = {
        key: package[key]
        for key in ("name", "version", "license", "dependencies", "devDependencies")
    }
    return {
        "lockfileVersion": 3,
        "packages": {
            "": root,
            "node_modules/@playwright/test": {
                "resolved": "https://registry.invalid/playwright.tgz",
                "integrity": "sha512-YWJjZA==",
            },
        },
    }


def _write_json(path: Path, document: object) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def test_cockpit_ci_guard_passes_the_current_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--check"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "npm lock integrity passed" in result.stdout


def test_lock_guard_accepts_aligned_v3_integrity(tmp_path: Path) -> None:
    package = _package()
    package_path = tmp_path / "package.json"
    lock_path = tmp_path / "package-lock.json"
    _write_json(package_path, package)
    _write_json(lock_path, _lock(package))

    assert guard.audit_lockfile(package_path, lock_path) == ()


def test_lock_guard_reports_drift_scripts_version_and_integrity(tmp_path: Path) -> None:
    package = _package()
    package["scripts"] = {"build": "build"}
    lock = _lock(package)
    lock["lockfileVersion"] = 2
    packages = lock["packages"]
    assert isinstance(packages, dict)
    root = packages[""]
    assert isinstance(root, dict)
    root["version"] = "stale"
    playwright = packages["node_modules/@playwright/test"]
    assert isinstance(playwright, dict)
    playwright.pop("integrity")
    package_path = tmp_path / "package.json"
    lock_path = tmp_path / "package-lock.json"
    _write_json(package_path, package)
    _write_json(lock_path, lock)

    codes = {finding.code for finding in guard.audit_lockfile(package_path, lock_path)}

    assert codes == {
        "lock-root-drift",
        "lock-version-invalid",
        "package-script-missing",
        "registry-integrity-missing",
    }


def test_lock_guard_fails_closed_for_missing_and_malformed_documents(tmp_path: Path) -> None:
    package = tmp_path / "package.json"
    lockfile = tmp_path / "package-lock.json"
    package.write_text("[]", encoding="utf-8")
    lockfile.write_text("{", encoding="utf-8")

    assert [finding.code for finding in guard.audit_lockfile(package, lockfile)] == [
        "json-root-invalid",
        "json-invalid",
    ]
    assert (
        guard.audit_lockfile(tmp_path / "absent-package.json", lockfile)[0].code == "file-missing"
    )


def test_workflow_guard_names_every_missing_gate(tmp_path: Path) -> None:
    workflow = tmp_path / "clients-cockpit.yml"
    workflow.write_text("name: clients-cockpit\nrun: npm ci\n", encoding="utf-8")

    findings = guard.audit_workflow(workflow)

    assert [finding.code for finding in findings] == ["workflow-gate-missing"]
    assert "npm run coverage" in findings[0].detail
    assert "dashboard_operator_writes.py" in findings[0].detail
    assert guard.audit_workflow(tmp_path / "missing.yml")[0].code == "workflow-missing"


def test_workflow_guard_accepts_the_complete_contract(tmp_path: Path) -> None:
    workflow = tmp_path / "clients-cockpit.yml"
    workflow.write_text("\n".join(guard.REQUIRED_WORKFLOW_MARKERS), encoding="utf-8")

    assert guard.audit_workflow(workflow) == ()
