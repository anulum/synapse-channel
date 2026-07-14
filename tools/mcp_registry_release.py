#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — MCP package and official-registry release evidence
"""Collect immutable MCP distribution evidence from released artefacts.

The official MCP Registry stores metadata rather than packages. A valid release
therefore has two ordered gates: the exact PyPI package must exist first, then the
registry must expose an active latest record whose server and package versions
match ``server.json``. This command checks those public boundaries without
publishing or authenticating.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import quote, urlencode, urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_NAME = "io.github.anulum/synapse-channel"
REGISTRY_SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
PYPI_PACKAGE = "synapse-channel"
OWNERSHIP_MARKER = f"mcp-name: {REGISTRY_NAME}"
OFFICIAL_PYPI_API = "https://pypi.org/pypi"
OFFICIAL_REGISTRY_API = "https://registry.modelcontextprotocol.io/v0.1/servers"

Phase = Literal["package", "registry"]
JsonFetcher = Callable[[str, float], Mapping[str, object]]


class _TomlModule(Protocol):
    """Structural type shared by ``tomllib`` and its ``tomli`` backport."""

    def loads(self, value: str, /) -> dict[str, object]:
        """Decode one TOML document."""


class ReleaseContractError(ValueError):
    """Raised when local release metadata is internally inconsistent."""


class VerificationUnavailable(RuntimeError):
    """Raised when public verification evidence cannot be retrieved or decoded."""


@dataclass(frozen=True)
class ReleaseContract:
    """Immutable local identity and version expected on public registries."""

    name: str
    version: str
    package: str


@dataclass(frozen=True)
class VerificationResult:
    """Structured package and registry evidence for one exact version."""

    phase: Phase
    version: str
    pypi_url: str
    pypi_file_types: tuple[str, ...]
    registry_url: str
    registry_status: str | None
    registry_is_latest: bool | None
    registry_published_at: str | None
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Return whether every requested public boundary matched."""
        return not self.errors

    def as_json(self) -> dict[str, object]:
        """Return a stable JSON-compatible evidence object."""
        return {
            "ok": self.ok,
            "phase": self.phase,
            "version": self.version,
            "pypi_url": self.pypi_url,
            "pypi_file_types": list(self.pypi_file_types),
            "registry_url": self.registry_url,
            "registry_status": self.registry_status,
            "registry_is_latest": self.registry_is_latest,
            "registry_published_at": self.registry_published_at,
            "errors": list(self.errors),
        }


def _mapping(value: object) -> Mapping[str, object] | None:
    """Narrow a decoded JSON object to a string-keyed mapping."""
    if not isinstance(value, dict):
        return None
    return cast(Mapping[str, object], value)


def _read_json(path: Path) -> Mapping[str, object]:
    """Read one local JSON object or raise a release-contract error."""
    try:
        decoded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseContractError(f"cannot read {path}: {exc}") from exc
    mapping = _mapping(decoded)
    if mapping is None:
        raise ReleaseContractError(f"{path} must contain a JSON object")
    return mapping


def _read_toml(path: Path) -> Mapping[str, object]:
    """Read TOML with the standard parser or the supported Python 3.10 backport."""
    raw = path.read_text(encoding="utf-8")
    for module_name in ("tomllib", "tomli"):
        try:
            parser = cast(_TomlModule, importlib.import_module(module_name))
        except ModuleNotFoundError:
            continue
        return parser.loads(raw)
    raise ModuleNotFoundError("TOML metadata needs Python 3.11+ or the 'tomli' package")


def load_contract(root: Path = REPO_ROOT, *, expect_version: str | None = None) -> ReleaseContract:
    """Load and cross-check the local MCP release identity."""
    server = _read_json(root / "server.json")
    try:
        project_data = _read_toml(root / "pyproject.toml")
        project = project_data["project"]
        readme = (root / "README.md").read_text(encoding="utf-8")
    except (OSError, KeyError, ModuleNotFoundError, ValueError) as exc:
        raise ReleaseContractError(f"cannot read package metadata: {exc}") from exc

    if not isinstance(project, dict):
        raise ReleaseContractError("pyproject.toml project metadata must be a table")

    packages = server.get("packages")
    package = _mapping(packages[0]) if isinstance(packages, list) and len(packages) == 1 else None
    project_version = str(project.get("version", ""))
    server_version = str(server.get("version", ""))
    errors: list[str] = []
    if server.get("$schema") != REGISTRY_SCHEMA:
        errors.append("server.json schema is not the verified 2025-12-11 schema")
    if server.get("name") != REGISTRY_NAME:
        errors.append(f"server.json name is not {REGISTRY_NAME}")
    if not project_version or server_version != project_version:
        errors.append("server.json and pyproject.toml versions do not match")
    if expect_version is not None and project_version != expect_version:
        errors.append(f"local version is {project_version!r}, expected {expect_version!r}")
    if OWNERSHIP_MARKER not in readme:
        errors.append("README is missing the exact PyPI MCP ownership marker")
    if package is None:
        errors.append("server.json must contain exactly one package")
    elif (
        package.get("registryType") != "pypi"
        or package.get("identifier") != PYPI_PACKAGE
        or package.get("version") != project_version
        or package.get("transport") != {"type": "stdio"}
    ):
        errors.append("server.json PyPI package identity, version, or transport drifted")
    if errors:
        raise ReleaseContractError("; ".join(errors))
    return ReleaseContract(name=REGISTRY_NAME, version=project_version, package=PYPI_PACKAGE)


def fetch_json(url: str, timeout: float) -> Mapping[str, object]:
    """Fetch a public JSON object with bounded network and decoding failures."""
    parsed = urlsplit(url)
    connection_type = {"http": HTTPConnection, "https": HTTPSConnection}.get(parsed.scheme)
    if connection_type is None or parsed.hostname is None:
        raise VerificationUnavailable(f"refusing non-HTTP evidence URL: {url}")
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    try:
        connection = connection_type(parsed.hostname, port=parsed.port, timeout=timeout)
    except ValueError as exc:
        raise VerificationUnavailable(f"invalid HTTP evidence URL {url}: {exc}") from exc
    try:
        connection.request(
            "GET",
            target,
            headers={
                "Accept": "application/json",
                "User-Agent": "synapse-channel-release-audit",
            },
        )
        response = connection.getresponse()
        payload = response.read()
        if not 200 <= response.status < 300:
            raise VerificationUnavailable(f"HTTP {response.status} from {url}")
    except VerificationUnavailable:
        raise
    except (HTTPException, TimeoutError, OSError, ValueError) as exc:
        raise VerificationUnavailable(f"cannot fetch {url}: {exc}") from exc
    finally:
        connection.close()
    try:
        decoded: object = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationUnavailable(f"invalid JSON from {url}: {exc}") from exc
    mapping = _mapping(decoded)
    if mapping is None:
        raise VerificationUnavailable(f"JSON from {url} is not an object")
    return mapping


def _pypi_url(base: str, contract: ReleaseContract) -> str:
    """Return the exact-version PyPI JSON endpoint."""
    package = quote(contract.package, safe="")
    version = quote(contract.version, safe="")
    return f"{base.rstrip('/')}/{package}/{version}/json"


def _registry_url(endpoint: str, contract: ReleaseContract) -> str:
    """Return the official registry search endpoint for the exact server name."""
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}{urlencode({'search': contract.name})}"


def _verify_pypi(
    contract: ReleaseContract,
    *,
    base: str,
    timeout: float,
    fetcher: JsonFetcher,
) -> tuple[str, tuple[str, ...], list[str]]:
    """Return PyPI evidence and every package-contract mismatch."""
    url = _pypi_url(base, contract)
    payload = fetcher(url, timeout)
    info = _mapping(payload.get("info"))
    files = payload.get("urls")
    errors: list[str] = []
    if info is None:
        errors.append("PyPI response has no package info object")
        description = ""
    else:
        if info.get("version") != contract.version:
            errors.append("PyPI package version does not match server.json")
        description = str(info.get("description", ""))
    if OWNERSHIP_MARKER not in description:
        errors.append("PyPI package description lacks the exact MCP ownership marker")

    file_values = files if isinstance(files, list) else []
    file_types = tuple(
        sorted(
            {
                str(item.get("packagetype"))
                for value in file_values
                if (item := _mapping(value)) is not None and item.get("packagetype") is not None
            }
        )
    )
    missing = {"bdist_wheel", "sdist"}.difference(file_types)
    if missing:
        errors.append(f"PyPI release lacks required artefacts: {', '.join(sorted(missing))}")
    return url, file_types, errors


def _verify_registry(
    contract: ReleaseContract,
    *,
    endpoint: str,
    timeout: float,
    fetcher: JsonFetcher,
) -> tuple[str, str | None, bool | None, str | None, list[str]]:
    """Return official-registry evidence and exact immutable-record mismatches."""
    url = _registry_url(endpoint, contract)
    payload = fetcher(url, timeout)
    servers = payload.get("servers")
    exact: Mapping[str, object] | None = None
    if isinstance(servers, list):
        for value in servers:
            entry = _mapping(value)
            server = _mapping(entry.get("server")) if entry is not None else None
            if (
                server is not None
                and server.get("name") == contract.name
                and server.get("version") == contract.version
            ):
                exact = entry
                break
    if exact is None:
        return url, None, None, None, ["official registry has no exact-version server record"]

    server = _mapping(exact.get("server"))
    meta = _mapping(exact.get("_meta"))
    official = (
        _mapping(meta.get("io.modelcontextprotocol.registry/official"))
        if meta is not None
        else None
    )
    packages = server.get("packages") if server is not None else None
    package = _mapping(packages[0]) if isinstance(packages, list) and len(packages) == 1 else None
    status = (
        str(official.get("status")) if official and official.get("status") is not None else None
    )
    is_latest = official.get("isLatest") if official is not None else None
    latest = is_latest if isinstance(is_latest, bool) else None
    published = (
        str(official.get("publishedAt"))
        if official and official.get("publishedAt") is not None
        else None
    )
    errors: list[str] = []
    if package is None or (
        package.get("registryType") != "pypi"
        or package.get("identifier") != contract.package
        or package.get("version") != contract.version
        or package.get("transport") != {"type": "stdio"}
    ):
        errors.append("official registry package identity, version, or transport drifted")
    if status != "active":
        errors.append("official registry record is not active")
    if latest is not True:
        errors.append("official registry record is not marked latest")
    if not published:
        errors.append("official registry record lacks its publication timestamp")
    return url, status, latest, published, errors


def verify_distribution(
    contract: ReleaseContract,
    *,
    phase: Phase,
    timeout: float = 20.0,
    fetcher: JsonFetcher = fetch_json,
    pypi_api_base: str = OFFICIAL_PYPI_API,
    registry_api: str = OFFICIAL_REGISTRY_API,
) -> VerificationResult:
    """Verify the released PyPI package and, when requested, registry record."""
    pypi_url, file_types, errors = _verify_pypi(
        contract,
        base=pypi_api_base,
        timeout=timeout,
        fetcher=fetcher,
    )
    registry_url = _registry_url(registry_api, contract)
    status: str | None = None
    latest: bool | None = None
    published: str | None = None
    if phase == "registry":
        registry_url, status, latest, published, registry_errors = _verify_registry(
            contract,
            endpoint=registry_api,
            timeout=timeout,
            fetcher=fetcher,
        )
        errors.extend(registry_errors)
    return VerificationResult(
        phase=phase,
        version=contract.version,
        pypi_url=pypi_url,
        pypi_file_types=file_types,
        registry_url=registry_url,
        registry_status=status,
        registry_is_latest=latest,
        registry_published_at=published,
        errors=tuple(errors),
    )
