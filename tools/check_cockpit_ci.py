#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — offline cockpit workflow and npm-lock integrity guard
"""Guard the dedicated cockpit CI contract and its npm lockfile offline."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PACKAGE = REPO_ROOT / "clients" / "cockpit" / "package.json"
DEFAULT_LOCKFILE = REPO_ROOT / "clients" / "cockpit" / "package-lock.json"
DEFAULT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "clients-cockpit.yml"
INTEGRITY_PATTERN = re.compile(r"^sha(?:256|384|512)-[A-Za-z0-9+/=]+$")

REQUIRED_SCRIPTS = ("typecheck", "coverage", "build", "e2e")
REQUIRED_WORKFLOW_MARKERS = (
    "clients/cockpit/**",
    "clients/cockpit/package-lock.json",
    "src/synapse_channel/dashboard.py",
    "src/synapse_channel/dashboard_bind.py",
    "src/synapse_channel/dashboard_feed_serving.py",
    "src/synapse_channel/dashboard_operator_writes.py",
    "src/synapse_channel/cli_dashboard.py",
    ".github/workflows/clients-cockpit.yml",
    "cache-dependency-path: clients/cockpit/package-lock.json",
    "npm ci",
    "npm run typecheck",
    "npm run coverage",
    "npm run build",
    "playwright install --with-deps chromium",
    "npm run e2e",
    "if: failure()",
    "actions/upload-artifact@",
    "clients/cockpit/test-results/",
)


@dataclass(frozen=True)
class Finding:
    """One stable cockpit CI guard finding."""

    code: str
    path: Path
    detail: str

    def format(self) -> str:
        """Render this finding as one diagnostic line."""
        return f"{self.code}: {self.path}: {self.detail}"


@dataclass(frozen=True)
class CliArgs:
    """Resolved file arguments for the cockpit CI guard."""

    package: Path
    lockfile: Path
    workflow: Path


def _read_object(path: Path) -> tuple[dict[str, object] | None, Finding | None]:
    if not path.is_file():
        return None, Finding("file-missing", path, "required file does not exist")
    try:
        parsed = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, Finding("json-invalid", path, str(exc))
    if not isinstance(parsed, dict):
        return None, Finding("json-root-invalid", path, "root must be an object")
    return cast(dict[str, object], parsed), None


def _string_map(raw: object) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return None
        result[key] = value
    return result


def audit_lockfile(package_path: Path, lockfile_path: Path) -> tuple[Finding, ...]:
    """Return findings for package/lock drift and missing registry integrity.

    Parameters
    ----------
    package_path : pathlib.Path
        Cockpit ``package.json`` path.
    lockfile_path : pathlib.Path
        Cockpit npm lockfile path.

    Returns
    -------
    tuple[Finding, ...]
        Structural, dependency-drift, or integrity findings.
    """
    package, package_error = _read_object(package_path)
    lockfile, lockfile_error = _read_object(lockfile_path)
    errors = tuple(error for error in (package_error, lockfile_error) if error is not None)
    if errors:
        return errors
    assert package is not None and lockfile is not None

    findings: list[Finding] = []
    scripts = _string_map(package.get("scripts"))
    missing_scripts = [name for name in REQUIRED_SCRIPTS if scripts is None or name not in scripts]
    if missing_scripts:
        findings.append(Finding("package-script-missing", package_path, ", ".join(missing_scripts)))
    if lockfile.get("lockfileVersion") != 3:
        findings.append(Finding("lock-version-invalid", lockfile_path, "expected npm lockfile v3"))

    packages_raw = lockfile.get("packages")
    if not isinstance(packages_raw, dict):
        findings.append(
            Finding("lock-packages-invalid", lockfile_path, "packages must be an object")
        )
        return tuple(findings)
    packages = cast(dict[str, object], packages_raw)
    root_raw = packages.get("")
    if not isinstance(root_raw, dict):
        findings.append(Finding("lock-root-missing", lockfile_path, "packages[''] is absent"))
        return tuple(findings)
    root = cast(dict[str, object], root_raw)
    for field in ("name", "version", "license", "dependencies", "devDependencies"):
        if package.get(field) != root.get(field):
            findings.append(
                Finding("lock-root-drift", lockfile_path, f"root field {field} differs")
            )

    if "node_modules/@playwright/test" not in packages:
        findings.append(
            Finding("playwright-lock-missing", lockfile_path, "@playwright/test is not resolved")
        )
    for package_name, node_raw in packages.items():
        if package_name == "" or not isinstance(node_raw, dict):
            continue
        node = cast(dict[str, object], node_raw)
        resolved = node.get("resolved")
        if not isinstance(resolved, str) or not resolved.startswith("https://"):
            continue
        integrity = node.get("integrity")
        if not isinstance(integrity, str) or INTEGRITY_PATTERN.fullmatch(integrity) is None:
            findings.append(Finding("registry-integrity-missing", lockfile_path, package_name))
    return tuple(findings)


def audit_workflow(path: Path) -> tuple[Finding, ...]:
    """Return findings when the dedicated cockpit workflow loses a required gate.

    Parameters
    ----------
    path : pathlib.Path
        Dedicated cockpit workflow path.

    Returns
    -------
    tuple[Finding, ...]
        Missing-file or missing-marker findings.
    """
    if not path.is_file():
        return (Finding("workflow-missing", path, "dedicated cockpit workflow is absent"),)
    text = path.read_text(encoding="utf-8")
    missing = [marker for marker in REQUIRED_WORKFLOW_MARKERS if marker not in text]
    if not missing:
        return ()
    return (Finding("workflow-gate-missing", path, ", ".join(missing)),)


def parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    """Parse optional repository-path overrides for the guard."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Run in check-only mode.")
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--lockfile", type=Path, default=DEFAULT_LOCKFILE)
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    namespace = parser.parse_args(argv)
    _ = namespace.check
    return CliArgs(
        package=namespace.package,
        lockfile=namespace.lockfile,
        workflow=namespace.workflow,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the offline cockpit CI guard and return a process exit code."""
    args = parse_args(argv)
    findings = (*audit_lockfile(args.package, args.lockfile), *audit_workflow(args.workflow))
    if findings:
        for finding in findings:
            print(finding.format(), file=sys.stderr)
        return 1
    print("cockpit CI contract and npm lock integrity passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
