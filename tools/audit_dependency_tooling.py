#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — audit dependency, tooling, workflow, and PyPI metadata surfaces
"""Audit local dependency and tooling maintenance surfaces.

The checker is intentionally offline and dependency-free. It verifies that the
repository's own maintenance surfaces still cover the expected local gates,
GitHub Actions pinning, Dependabot ecosystems, and PyPI publish/download
metadata. It does not contact PyPI or GitHub; remote freshness belongs to CI and
release monitoring, while this script catches repository drift before a push.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):  # pragma: no cover - version branch.
    import tomllib
else:  # pragma: no cover - covered on Python 3.10.
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PREFLIGHT = REPO_ROOT / "tools" / "preflight.sh"
DEFAULT_WORKFLOWS = REPO_ROOT / ".github" / "workflows"
DEFAULT_DEPENDABOT = REPO_ROOT / ".github" / "dependabot.yml"
DEFAULT_PYPROJECT = REPO_ROOT / "pyproject.toml"
DEFAULT_PUBLISH_WORKFLOW = DEFAULT_WORKFLOWS / "publish.yml"
DEFAULT_DOWNLOADS_WORKFLOW = DEFAULT_WORKFLOWS / "pypi-downloads.yml"
DEFAULT_DOWNLOADS_TOOL = REPO_ROOT / "tools" / "pypi_downloads.py"

FULL_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
USES_PATTERN = re.compile(r"^\s*-?\s*uses:\s*(?P<value>[^#\s]+)")
ECOSYSTEM_PATTERN = re.compile(r'package-ecosystem:\s*["\']?(?P<value>[^"\'\s]+)')

REQUIRED_PREFLIGHT_GATES = {
    "ruff format": ("ruff", "format"),
    "ruff check": ("ruff", "check"),
    "mypy": ("mypy",),
    "pytest": ("pytest",),
    "bandit": ("bandit",),
    "pip-audit": ("pip_audit",),
    "mkdocs": ("mkdocs", "build", "--strict"),
    "dev dependency drift": ("check_dev_dependency_drift.py", "--check"),
    "dependency tooling audit": ("audit_dependency_tooling.py", "--check"),
}
"""Preflight gates that must stay wired into the local push checklist."""

REQUIRED_DEPENDABOT_ECOSYSTEMS = {"github-actions", "pip", "docker"}
"""Dependabot ecosystems that keep workflow, Python, and image surfaces current."""

REQUIRED_PYPI_URLS = {"Repository", "Documentation", "Changelog", "Issues"}
"""Project URL labels expected in package metadata."""


@dataclass(frozen=True)
class Finding:
    """One dependency/tooling audit finding.

    Parameters
    ----------
    code : str
        Stable machine-readable finding code.
    path : Path
        File or directory where the finding was detected.
    detail : str
        Human-readable finding detail.
    """

    code: str
    path: Path
    detail: str

    def format(self) -> str:
        """Render the finding as one stable diagnostic line."""
        return f"{self.code}: {self.path}: {self.detail}"


@dataclass(frozen=True)
class CliArgs:
    """Parsed command-line arguments for the audit command."""

    check: bool
    preflight: Path
    workflows: Path
    dependabot: Path
    pyproject: Path
    publish_workflow: Path
    downloads_workflow: Path
    downloads_tool: Path


def audit_preflight(path: Path) -> tuple[Finding, ...]:
    """Return findings for missing local preflight gate commands.

    Parameters
    ----------
    path : Path
        Preflight shell script to inspect.

    Returns
    -------
    tuple[Finding, ...]
        Missing-file or missing-gate findings.
    """
    if not path.exists():
        return (Finding("preflight-missing", path, "preflight script does not exist"),)
    content = path.read_text(encoding="utf-8")
    missing = [
        label
        for label, markers in REQUIRED_PREFLIGHT_GATES.items()
        if not all(marker in content for marker in markers)
    ]
    if not missing:
        return ()
    return (
        Finding(
            "preflight-gate-missing",
            path,
            "missing gate(s): " + ", ".join(sorted(missing)),
        ),
    )


def audit_workflow_action_pins(workflows_dir: Path) -> tuple[Finding, ...]:
    """Return findings for workflow ``uses:`` entries not pinned to full SHAs.

    Parameters
    ----------
    workflows_dir : Path
        Directory containing GitHub Actions workflow YAML files.

    Returns
    -------
    tuple[Finding, ...]
        Findings for tag-pinned or unpinned action references.
    """
    if not workflows_dir.exists():
        return (
            Finding("workflow-dir-missing", workflows_dir, "workflow directory does not exist"),
        )
    findings: list[Finding] = []
    for workflow in sorted(workflows_dir.glob("*.yml")):
        for line_number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            match = USES_PATTERN.match(line)
            if match is None:
                continue
            value = match.group("value").strip("\"'")
            if value.startswith("./"):
                continue
            if "@" not in value:
                findings.append(
                    Finding(
                        "workflow-action-unpinned",
                        workflow,
                        f"line {line_number}: {value}",
                    )
                )
                continue
            _action, ref = value.rsplit("@", maxsplit=1)
            if not FULL_SHA_PATTERN.fullmatch(ref):
                findings.append(
                    Finding(
                        "workflow-action-not-sha-pinned",
                        workflow,
                        f"line {line_number}: {value}",
                    )
                )
    return tuple(findings)


def audit_dependabot(path: Path) -> tuple[Finding, ...]:
    """Return findings for missing Dependabot ecosystem coverage.

    Parameters
    ----------
    path : Path
        Dependabot configuration file.

    Returns
    -------
    tuple[Finding, ...]
        Missing-file or missing-ecosystem findings.
    """
    if not path.exists():
        return (Finding("dependabot-missing", path, "Dependabot config does not exist"),)
    ecosystems = {
        match.group("value")
        for match in ECOSYSTEM_PATTERN.finditer(path.read_text(encoding="utf-8"))
    }
    missing = sorted(REQUIRED_DEPENDABOT_ECOSYSTEMS - ecosystems)
    if not missing:
        return ()
    return (
        Finding(
            "dependabot-ecosystem-missing",
            path,
            "missing ecosystem(s): " + ", ".join(missing),
        ),
    )


def audit_pypi_metadata(
    *,
    pyproject: Path,
    publish_workflow: Path,
    downloads_workflow: Path,
    downloads_tool: Path,
) -> tuple[Finding, ...]:
    """Return findings for package metadata, publish, and download tracking.

    Parameters
    ----------
    pyproject : Path
        Project metadata file.
    publish_workflow : Path
        GitHub Actions workflow that publishes distributions to PyPI.
    downloads_workflow : Path
        GitHub Actions workflow that records PyPI download snapshots.
    downloads_tool : Path
        Local PyPI download snapshot tool invoked by the workflow.

    Returns
    -------
    tuple[Finding, ...]
        Findings for missing metadata and release tracking surfaces.
    """
    findings: list[Finding] = []
    project = _load_project_metadata(pyproject)
    required_fields = ("name", "version", "description", "readme", "license")
    missing_fields = [field for field in required_fields if not project.get(field)]
    if missing_fields:
        findings.append(
            Finding(
                "pypi-metadata-missing",
                pyproject,
                "missing field(s): " + ", ".join(missing_fields),
            )
        )
    urls = project.get("urls", {})
    present_urls = set(urls) if isinstance(urls, dict) else set()
    missing_urls = sorted(REQUIRED_PYPI_URLS - present_urls)
    if missing_urls:
        findings.append(
            Finding("pypi-url-missing", pyproject, "missing URL(s): " + ", ".join(missing_urls))
        )
    publish_text = _read_optional(publish_workflow)
    if "pypa/gh-action-pypi-publish" not in publish_text or "id-token: write" not in publish_text:
        findings.append(
            Finding(
                "pypi-trusted-publish-missing",
                publish_workflow,
                "publish workflow must use pypa/gh-action-pypi-publish with id-token: write",
            )
        )
    downloads_text = _read_optional(downloads_workflow)
    if "tools/pypi_downloads.py" not in downloads_text:
        findings.append(
            Finding(
                "pypi-download-tracker-missing",
                downloads_workflow,
                "download tracker workflow must invoke tools/pypi_downloads.py",
            )
        )
    if not downloads_tool.exists():
        findings.append(
            Finding(
                "pypi-download-tool-missing",
                downloads_tool,
                "download tracker tool does not exist",
            )
        )
    return tuple(findings)


def audit_repository(args: CliArgs) -> tuple[Finding, ...]:
    """Run every dependency/tooling audit check for ``args``.

    Parameters
    ----------
    args : CliArgs
        Parsed audit paths.

    Returns
    -------
    tuple[Finding, ...]
        All findings in stable category order.
    """
    return (
        *audit_preflight(args.preflight),
        *audit_workflow_action_pins(args.workflows),
        *audit_dependabot(args.dependabot),
        *audit_pypi_metadata(
            pyproject=args.pyproject,
            publish_workflow=args.publish_workflow,
            downloads_workflow=args.downloads_workflow,
            downloads_tool=args.downloads_tool,
        ),
    )


def parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    """Parse command-line arguments for the dependency/tooling audit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Run in check-only mode.")
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--workflows", type=Path, default=DEFAULT_WORKFLOWS)
    parser.add_argument("--dependabot", type=Path, default=DEFAULT_DEPENDABOT)
    parser.add_argument("--pyproject", type=Path, default=DEFAULT_PYPROJECT)
    parser.add_argument("--publish-workflow", type=Path, default=DEFAULT_PUBLISH_WORKFLOW)
    parser.add_argument("--downloads-workflow", type=Path, default=DEFAULT_DOWNLOADS_WORKFLOW)
    parser.add_argument("--downloads-tool", type=Path, default=DEFAULT_DOWNLOADS_TOOL)
    namespace = parser.parse_args(argv)
    return CliArgs(
        check=bool(namespace.check),
        preflight=namespace.preflight,
        workflows=namespace.workflows,
        dependabot=namespace.dependabot,
        pyproject=namespace.pyproject,
        publish_workflow=namespace.publish_workflow,
        downloads_workflow=namespace.downloads_workflow,
        downloads_tool=namespace.downloads_tool,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the dependency/tooling audit and return a process exit code."""
    args = parse_args(argv)
    _ = args.check
    findings = audit_repository(args)
    if findings:
        for finding in findings:
            print(finding.format(), file=sys.stderr)
        return 1
    workflow_count = len(list(args.workflows.glob("*.yml"))) if args.workflows.exists() else 0
    print(f"dependency/tooling audit passed: {workflow_count} workflow(s) scanned")
    return 0


def _load_project_metadata(path: Path) -> dict[str, object]:
    """Load the ``[project]`` table from ``path``."""
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        pyproject = tomllib.load(handle)
    project = pyproject.get("project", {})
    return dict(project) if isinstance(project, dict) else {}


def _read_optional(path: Path) -> str:
    """Return file text, or an empty string when ``path`` is absent."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
