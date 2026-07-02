# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dependency-manifest and CODEOWNERS reader for one repository
"""Read one repository's declared packages, dependencies, and owners.

This is the scanning half of the cross-repository dependency graph: given a
checkout directory, it reads the dependency manifests the ecosystem actually
uses — ``pyproject.toml`` (Python), ``Cargo.toml`` (Rust), ``package.json``
(JavaScript), ``go.mod`` (Go) — plus ``CODEOWNERS``, into one
:class:`RepoManifest` record. Everything stays declaration-level: package
names and dependency names as written by the repository, never resolved
versions, lockfiles, or network lookups.

TOML manifests need a TOML parser: the standard-library ``tomllib`` on
Python 3.11+ or the ``tomli`` backport. When neither is importable the TOML
manifests of a repository are reported in :attr:`RepoManifest.problems`
instead of being silently skipped, so a graph built from the scan states
what it could not see.
"""

from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PYTHON_ECOSYSTEM = "python"
RUST_ECOSYSTEM = "rust"
JAVASCRIPT_ECOSYSTEM = "javascript"
GO_ECOSYSTEM = "go"

MANIFEST_FILENAMES = ("pyproject.toml", "Cargo.toml", "package.json", "go.mod")
"""Dependency manifests a repository scan recognises, one per ecosystem."""

CODEOWNERS_LOCATIONS = (
    Path(".github") / "CODEOWNERS",
    Path("CODEOWNERS"),
    Path("docs") / "CODEOWNERS",
)
"""CODEOWNERS locations in the order GitHub resolves them."""

_REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_GO_REQUIRE_LINE_RE = re.compile(r"^require\s+(\S+)\s+\S+")
_GO_MODULE_RE = re.compile(r"^module\s+(\S+)")


@dataclass(frozen=True)
class ManifestPackage:
    """One package name a repository declares it provides.

    Attributes
    ----------
    name : str
        Normalised package name (PEP 503 for Python, lower-case for the
        other ecosystems).
    ecosystem : str
        ``python``, ``rust``, ``javascript``, or ``go``.
    manifest : str
        Repository-relative manifest path that declared the name.
    """

    name: str
    ecosystem: str
    manifest: str


@dataclass(frozen=True)
class ManifestDependency:
    """One dependency name a repository declares it consumes.

    Attributes
    ----------
    name : str
        Normalised dependency name, comparable against
        :attr:`ManifestPackage.name` within the same ecosystem.
    ecosystem : str
        ``python``, ``rust``, ``javascript``, or ``go``.
    manifest : str
        Repository-relative manifest path that declared the dependency.
    """

    name: str
    ecosystem: str
    manifest: str


@dataclass(frozen=True)
class RepoManifest:
    """Declaration-level dependency surface of one repository checkout.

    Attributes
    ----------
    repo : str
        Repository identity — the checkout directory name, which is also the
        ``worktree`` value claims carry on the coordination bus.
    path : str
        Absolute checkout path the scan read.
    packages : tuple[ManifestPackage, ...]
        Package names the repository provides, deterministic order.
    dependencies : tuple[ManifestDependency, ...]
        Dependency names the repository consumes, deterministic order.
    owners : tuple[str, ...]
        Unique CODEOWNERS handles across every rule, sorted.
    problems : tuple[str, ...]
        Manifests that exist but could not be parsed, with the reason —
        the scan fails visible, never silent.
    """

    repo: str
    path: str
    packages: tuple[ManifestPackage, ...]
    dependencies: tuple[ManifestDependency, ...]
    owners: tuple[str, ...]
    problems: tuple[str, ...]


def normalise_python_name(name: str) -> str:
    """Return the PEP 503 normalised form of a Python distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str:
    """Extract the distribution name from one PEP 508 requirement string.

    Parameters
    ----------
    requirement : str
        A requirement as written in ``[project] dependencies``, e.g.
        ``"websockets>=12,<16"`` or ``"synapse-channel[mcp] ; python_version
        >= '3.11'"``.

    Returns
    -------
    str
        The PEP 503 normalised distribution name, or ``""`` when the string
        does not start with a name.
    """
    match = _REQUIREMENT_NAME_RE.match(requirement)
    return normalise_python_name(match.group(1)) if match else ""


def _toml_loads(raw: bytes) -> dict[str, Any]:
    """Parse TOML bytes with ``tomllib`` or the ``tomli`` backport.

    Raises
    ------
    ModuleNotFoundError
        When neither parser is importable (Python 3.10 without ``tomli``).
    """
    for module_name in ("tomllib", "tomli"):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        loaded: dict[str, Any] = module.loads(raw.decode("utf-8"))
        return loaded
    raise ModuleNotFoundError("TOML manifests need Python 3.11+ or the 'tomli' package")


def _python_manifest(
    data: dict[str, Any], manifest: str
) -> tuple[list[ManifestPackage], list[ManifestDependency]]:
    """Extract the declared package and dependencies from ``pyproject.toml`` data."""
    project = data.get("project")
    if not isinstance(project, dict):
        return [], []
    packages: list[ManifestPackage] = []
    name = project.get("name")
    if isinstance(name, str) and name.strip():
        packages.append(
            ManifestPackage(
                name=normalise_python_name(name.strip()),
                ecosystem=PYTHON_ECOSYSTEM,
                manifest=manifest,
            )
        )
    requirement_strings: list[str] = []
    dependencies = project.get("dependencies")
    if isinstance(dependencies, list):
        requirement_strings.extend(str(item) for item in dependencies)
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for extra in sorted(optional):
            group = optional[extra]
            if isinstance(group, list):
                requirement_strings.extend(str(item) for item in group)
    seen: set[str] = set()
    parsed: list[ManifestDependency] = []
    for requirement in requirement_strings:
        dependency = requirement_name(requirement)
        if dependency and dependency not in seen:
            seen.add(dependency)
            parsed.append(
                ManifestDependency(name=dependency, ecosystem=PYTHON_ECOSYSTEM, manifest=manifest)
            )
    return packages, parsed


def _rust_dependency_name(key: str, value: Any) -> str:
    """Return the registry crate name for one ``Cargo.toml`` dependency entry.

    A table entry may rename the crate (``foo = { package = "real-name" }``);
    the registry name is what other repositories declare, so it wins.
    """
    if isinstance(value, dict):
        package = value.get("package")
        if isinstance(package, str) and package.strip():
            return package.strip().lower()
    return key.strip().lower()


def _rust_manifest(
    data: dict[str, Any], manifest: str
) -> tuple[list[ManifestPackage], list[ManifestDependency]]:
    """Extract the declared crate and dependencies from ``Cargo.toml`` data."""
    packages: list[ManifestPackage] = []
    package_table = data.get("package")
    if isinstance(package_table, dict):
        name = package_table.get("name")
        if isinstance(name, str) and name.strip():
            packages.append(
                ManifestPackage(
                    name=name.strip().lower(), ecosystem=RUST_ECOSYSTEM, manifest=manifest
                )
            )
    seen: set[str] = set()
    dependencies: list[ManifestDependency] = []
    for table_name in ("dependencies", "dev-dependencies", "build-dependencies"):
        table = data.get(table_name)
        if not isinstance(table, dict):
            continue
        for key in table:
            crate = _rust_dependency_name(str(key), table[key])
            if crate and crate not in seen:
                seen.add(crate)
                dependencies.append(
                    ManifestDependency(name=crate, ecosystem=RUST_ECOSYSTEM, manifest=manifest)
                )
    return packages, dependencies


def _javascript_manifest(
    data: dict[str, Any], manifest: str
) -> tuple[list[ManifestPackage], list[ManifestDependency]]:
    """Extract the declared package and dependencies from ``package.json`` data."""
    packages: list[ManifestPackage] = []
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        packages.append(
            ManifestPackage(
                name=name.strip().lower(), ecosystem=JAVASCRIPT_ECOSYSTEM, manifest=manifest
            )
        )
    seen: set[str] = set()
    dependencies: list[ManifestDependency] = []
    for table_name in ("dependencies", "devDependencies", "peerDependencies"):
        table = data.get(table_name)
        if not isinstance(table, dict):
            continue
        for key in table:
            dependency = str(key).strip().lower()
            if dependency and dependency not in seen:
                seen.add(dependency)
                dependencies.append(
                    ManifestDependency(
                        name=dependency, ecosystem=JAVASCRIPT_ECOSYSTEM, manifest=manifest
                    )
                )
    return packages, dependencies


def _go_manifest(
    text: str, manifest: str
) -> tuple[list[ManifestPackage], list[ManifestDependency]]:
    """Extract the module path and requirements from ``go.mod`` text."""
    packages: list[ManifestPackage] = []
    seen: set[str] = set()
    dependencies: list[ManifestDependency] = []
    in_require_block = False
    for raw_line in text.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        module_match = _GO_MODULE_RE.match(line)
        if module_match and not packages:
            packages.append(
                ManifestPackage(
                    name=module_match.group(1), ecosystem=GO_ECOSYSTEM, manifest=manifest
                )
            )
            continue
        if line == "require (":
            in_require_block = True
            continue
        if in_require_block:
            if line == ")":
                in_require_block = False
                continue
            module_path = line.split()[0]
            if module_path not in seen:
                seen.add(module_path)
                dependencies.append(
                    ManifestDependency(name=module_path, ecosystem=GO_ECOSYSTEM, manifest=manifest)
                )
            continue
        require_match = _GO_REQUIRE_LINE_RE.match(line)
        if require_match and require_match.group(1) not in seen:
            seen.add(require_match.group(1))
            dependencies.append(
                ManifestDependency(
                    name=require_match.group(1), ecosystem=GO_ECOSYSTEM, manifest=manifest
                )
            )
    return packages, dependencies


def _codeowners_handles(repo_dir: Path) -> tuple[str, ...]:
    """Return the unique owner handles across every CODEOWNERS rule.

    Owners are the whitespace-separated tokens after each rule's pattern —
    ``@user``, ``@org/team``, or an email address. Only the first existing
    CODEOWNERS location is read, matching how GitHub resolves the file.
    """
    for location in CODEOWNERS_LOCATIONS:
        path = repo_dir / location
        if not path.is_file():
            continue
        handles: set[str] = set()
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.split("#", 1)[0].strip()
            tokens = line.split()
            if len(tokens) < 2:
                continue
            for token in tokens[1:]:
                if token.startswith("@") or "@" in token:
                    handles.add(token)
        return tuple(sorted(handles))
    return ()


def read_repo_manifest(repo_dir: Path) -> RepoManifest:
    """Read one repository checkout into a :class:`RepoManifest` record.

    Parameters
    ----------
    repo_dir : pathlib.Path
        The checkout directory. Its name is the repository identity — the
        same ``worktree`` value claims carry on the coordination bus.

    Returns
    -------
    RepoManifest
        Declared packages, dependencies, and owners; manifests that exist
        but could not be parsed are listed in ``problems``.
    """
    packages: list[ManifestPackage] = []
    dependencies: list[ManifestDependency] = []
    problems: list[str] = []
    for filename in MANIFEST_FILENAMES:
        path = repo_dir / filename
        if not path.is_file():
            continue
        try:
            if filename in ("pyproject.toml", "Cargo.toml"):
                data = _toml_loads(path.read_bytes())
                extractor = _python_manifest if filename == "pyproject.toml" else _rust_manifest
                found_packages, found_dependencies = extractor(data, filename)
            elif filename == "package.json":
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("top-level value is not an object")
                found_packages, found_dependencies = _javascript_manifest(loaded, filename)
            else:
                found_packages, found_dependencies = _go_manifest(
                    path.read_text(encoding="utf-8", errors="replace"), filename
                )
        except (ValueError, ModuleNotFoundError) as exc:
            problems.append(f"{filename}: {exc}")
            continue
        packages.extend(found_packages)
        dependencies.extend(found_dependencies)
    return RepoManifest(
        repo=repo_dir.name,
        path=str(repo_dir),
        packages=tuple(packages),
        dependencies=tuple(dependencies),
        owners=_codeowners_handles(repo_dir),
        problems=tuple(problems),
    )


def discover_repositories(root: Path) -> tuple[Path, ...]:
    """Return the immediate subdirectories of ``root`` that look like repositories.

    A directory qualifies when it holds at least one recognised dependency
    manifest, a CODEOWNERS file, or a ``.git`` entry. The result is sorted by
    name so a scan over the same tree is deterministic.
    """
    repositories: list[Path] = []
    for candidate in sorted(root.iterdir(), key=lambda path: path.name):
        if not candidate.is_dir():
            continue
        has_manifest = any((candidate / name).is_file() for name in MANIFEST_FILENAMES)
        has_codeowners = any((candidate / location).is_file() for location in CODEOWNERS_LOCATIONS)
        if has_manifest or has_codeowners or (candidate / ".git").exists():
            repositories.append(candidate)
    return tuple(repositories)
