# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — bounded public repository evidence inspection
"""Inspect GitHub metadata and text without following candidate download links."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from email.message import Message
from typing import Any, cast
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

GITHUB_API = "https://api.github.com"
MAX_RESPONSE_BYTES = 1_000_000
MAX_TEXT_BYTES = 100_000
MAX_TREE_ENTRIES = 5_000
_REPOSITORY = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")
_RANDOM_BLOB = re.compile(r"\.github/[A-Za-z]{14}")
_WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_-]+\.ya?ml")
_SOURCE_SUFFIXES = (
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".java",
    ".jl",
    ".js",
    ".jsx",
    ".kt",
    ".mojo",
    ".py",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
)
_SOURCE_MANIFESTS = frozenset(
    {"Cargo.toml", "Dockerfile", "go.mod", "package.json", "pyproject.toml"}
)


class InspectionError(ValueError):
    """A public metadata response is unavailable or violates the read boundary."""


class RepositoryMissing(InspectionError):
    """GitHub returned 404 for an exact public repository or metadata object."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        fp: object,
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        raise InspectionError("GitHub metadata redirect refused")


def _api_url(path: str) -> str:
    if not path.startswith(("/repos/", "/users/")) or any(
        part in {"", ".", ".."} for part in path[1:].split("/")
    ):
        raise InspectionError("GitHub metadata path is invalid")
    return GITHUB_API + path


def fetch_github_json(url: str) -> dict[str, Any]:
    """Read one bounded GitHub REST object with no redirects or token disclosure."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.github.com"
        or parsed.query not in {"", "recursive=1"}
        or parsed.fragment
        or not parsed.path.startswith(("/repos/", "/users/"))
    ):
        raise InspectionError("GitHub metadata URL is invalid")
    _api_url(parsed.path)
    token = os.environ.get("GITHUB_TOKEN")
    headers = {
        "User-Agent": "Synapse-Channel-Discovery/0.2",
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    try:
        with build_opener(_NoRedirect()).open(request, timeout=15) as response:
            body = cast(bytes, response.read(MAX_RESPONSE_BYTES + 1))
    except HTTPError as exc:
        if exc.code == 404:
            raise RepositoryMissing("GitHub metadata object absent") from exc
        raise InspectionError(f"GitHub metadata HTTP {exc.code}") from exc
    except OSError as exc:
        raise InspectionError("GitHub metadata request failed") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise InspectionError("GitHub metadata exceeded byte limit")
    try:
        value = json.loads(body)
    except (UnicodeError, ValueError) as exc:
        raise InspectionError("GitHub metadata JSON is invalid") from exc
    if not isinstance(value, dict):
        raise InspectionError("GitHub metadata root is invalid")
    return cast(dict[str, Any], value)


def _text_file(value: Mapping[str, Any]) -> str:
    if value.get("encoding") != "base64" or not isinstance(value.get("content"), str):
        raise InspectionError("GitHub text response is invalid")
    try:
        raw = base64.b64decode("".join(value["content"].split()), validate=True)
        if len(raw) > MAX_TEXT_BYTES:
            raise InspectionError("GitHub text exceeded byte limit")
        return raw.decode("utf-8")
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise InspectionError("GitHub text encoding is invalid") from exc


def _repository_name(value: object) -> str:
    if not isinstance(value, str) or _REPOSITORY.fullmatch(value) is None:
        raise InspectionError("GitHub repository name is invalid")
    if any(part.startswith(".") or part.endswith(".") for part in value.split("/")):
        raise InspectionError("GitHub repository name is invalid")
    return value


def repository_reachability(
    repository_url: object, *, fetch: Callable[[str], Mapping[str, Any]] = fetch_github_json
) -> str:
    """Check only a canonical GitHub repository URL via the fixed REST host."""
    if not isinstance(repository_url, str):
        return "not_declared"
    parsed = urlsplit(repository_url)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment:
        return "unsupported_url"
    try:
        name = _repository_name(parsed.path.strip("/"))
    except InspectionError:
        return "unsupported_url"
    try:
        result = fetch(_api_url(f"/repos/{name}"))
    except RepositoryMissing:
        return "unreachable"
    except InspectionError:
        return "unavailable"
    return (
        "reachable"
        if str(result.get("full_name", "")).casefold() == name.casefold()
        else "unavailable"
    )


def needs_lure_inspection(repository: Mapping[str, Any]) -> bool:
    """Select a bounded suspicious subset from search metadata alone."""
    language = repository.get("language")
    return (
        repository.get("has_pages") is True
        and repository.get("license") is None
        and (language is None or language == "HTML")
    )


def _source_paths(paths: list[str]) -> list[str]:
    return [
        path
        for path in paths
        if not path.startswith(".github/")
        and (path.endswith(_SOURCE_SUFFIXES) or path.split("/")[-1] in _SOURCE_MANIFESTS)
    ]


def inspect_host(
    repository: Mapping[str, Any], *, fetch: Callable[[str], Mapping[str, Any]] = fetch_github_json
) -> dict[str, Any]:
    """Classify source presence and the reviewed workflow-plus-Pages lure pattern."""
    name = _repository_name(repository.get("full_name"))
    branch = repository.get("default_branch")
    if not isinstance(branch, str) or not branch or len(branch) > 100:
        raise InspectionError("GitHub default branch is invalid")
    encoded_branch = quote(branch, safe="")
    tree = fetch(_api_url(f"/repos/{name}/git/trees/{encoded_branch}") + "?recursive=1")
    entries = tree.get("tree")
    if (
        tree.get("truncated") is not False
        or not isinstance(entries, list)
        or len(entries) > MAX_TREE_ENTRIES
    ):
        raise InspectionError("GitHub tree is incomplete")
    paths = [
        entry["path"]
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("type") == "blob"
        and isinstance(entry.get("path"), str)
    ]
    if len(paths) > MAX_TREE_ENTRIES:
        raise InspectionError("GitHub tree exceeded entry limit")
    sources = _source_paths(paths)
    result: dict[str, Any] = {
        "code_file_count": len(sources),
        "inspection": "code_present" if sources else "source_absent",
    }
    if sources:
        return result
    names = set(paths)
    blobs = [path for path in paths if _RANDOM_BLOB.fullmatch(path)]
    workflows = [path for path in paths if _WORKFLOW.fullmatch(path)]
    allowed = {"README.md", "index.html", *blobs, *workflows}
    allowed.update(path for path in paths if path.lower().endswith(".svg"))
    if (
        not needs_lure_inspection(repository)
        or not {"README.md", "index.html"} <= names
        or len(blobs) != 1
        or len(workflows) != 1
        or names - allowed
    ):
        return result
    readme = _text_file(fetch(_api_url(f"/repos/{name}/contents/README.md")))
    workflow = _text_file(fetch(_api_url(f"/repos/{name}/contents/{workflows[0]}")))
    owner, project = name.split("/", 1)
    pages = f"https://{owner}.github.io/{project}".casefold()
    if (
        "download link" in readme.casefold()
        and pages in readme.casefold()
        and re.search(r"\*/5\s+\*\s+\*\s+\*\s+\*", workflow)
        and re.search(r"contents\s*:\s*write", workflow)
        and re.search(r"\bgit\s+[^\n]{0,160}\bcommit\b", workflow)
        and blobs[0] in workflow
    ):
        result["inspection"] = "rejected_lure"
    return result


def host_provenance(
    repository: Mapping[str, Any], *, fetch: Callable[[str], Mapping[str, Any]] = fetch_github_json
) -> dict[str, Any]:
    """Read bounded owner-history and release-digest signals for review ranking."""
    name = _repository_name(repository.get("full_name"))
    owner = name.split("/", 1)[0]
    result: dict[str, Any] = {
        "owner_age_days": None,
        "owner_public_repos": None,
        "release_digest_present": False,
    }
    try:
        owner_data = fetch(_api_url(f"/users/{owner}"))
    except RepositoryMissing:
        owner_data = {}
    created = owner_data.get("created_at")
    if isinstance(created, str):
        try:
            created_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
            result["owner_age_days"] = max(0, (datetime.now(timezone.utc) - created_at).days)
        except ValueError:
            pass
    public_repos = owner_data.get("public_repos")
    if type(public_repos) is int and public_repos >= 0:
        result["owner_public_repos"] = public_repos
    try:
        release = fetch(_api_url(f"/repos/{name}/releases/latest"))
    except RepositoryMissing:
        return result
    assets = release.get("assets")
    if release.get("draft") is False and isinstance(assets, list):
        result["release_digest_present"] = any(
            isinstance(asset, dict)
            and isinstance(asset.get("digest"), str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", asset["digest"]) is not None
            for asset in assets
        )
    return result
