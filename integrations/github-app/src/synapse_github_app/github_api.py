# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — bounded fixed-origin GitHub REST client
"""Call the minimum GitHub App REST surface without redirecting credentials."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from http.client import HTTPMessage
from typing import IO, Protocol, cast
from urllib import error, parse, request

from synapse_github_app.auth import InstallationToken, parse_installation_token
from synapse_github_app.checks import CheckRunRequest
from synapse_github_app.errors import GitHubApiError, PayloadError
from synapse_github_app.json_boundary import loads_strict_bounded
from synapse_github_app.models import PullRequestSeed, Repository, normalize_paths

API_VERSION = "2026-03-10"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_OPEN_PULL_REQUESTS = 100
MAX_FILES_PER_PULL_REQUEST = 3000
PAGE_SIZE = 100


class _ReadableResponse(Protocol):
    """The urllib response surface consumed by this module."""

    headers: HTTPMessage

    def read(self, amount: int = -1) -> bytes:
        """Read at most ``amount`` bytes."""

    def close(self) -> None:
        """Close the response."""


class _NoRedirect(request.HTTPRedirectHandler):
    """Refuse redirects so bearer credentials cannot cross an origin."""

    def redirect_request(
        self,
        req: request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        """Return no follow-up request for every redirect status."""
        return None


@dataclass(frozen=True)
class PullRequestInventory:
    """Bounded open pull-request list and completeness flag."""

    items: tuple[PullRequestSeed, ...]
    truncated: bool


@dataclass(frozen=True)
class FileInventory:
    """Bounded changed-file list and completeness flag."""

    paths: tuple[str, ...]
    truncated: bool


def _json_list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise PayloadError(f"{field} must be an array")
    return cast(list[object], value)


def _canonical_api_url(value: str, *, allow_insecure_loopback: bool) -> str:
    stripped = value.strip()
    if not stripped.isprintable() or any(char.isspace() for char in stripped):
        raise GitHubApiError("GitHub API URL must not contain whitespace or control characters")
    parsed = parse.urlsplit(stripped)
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise GitHubApiError("GitHub API URL must be an absolute credential-free URL")
    if parsed.query or parsed.fragment:
        raise GitHubApiError("GitHub API URL must not contain query or fragment data")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise GitHubApiError("GitHub API URL contains an invalid port") from exc
    if parsed.scheme != "https":
        loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        if parsed.scheme != "http" or not allow_insecure_loopback or not loopback:
            raise GitHubApiError("GitHub API URL must use HTTPS")
    return parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


class GitHubApi:
    """Minimal, bounded client for installation, pull, file, and check endpoints."""

    def __init__(
        self,
        *,
        api_url: str = "https://api.github.com",
        timeout_seconds: float = 10.0,
        allow_insecure_loopback: bool = False,
    ) -> None:
        """Configure one fixed GitHub API origin."""
        if timeout_seconds <= 0 or timeout_seconds > 120:
            raise GitHubApiError("GitHub API timeout must be within (0, 120] seconds")
        self._api_url = _canonical_api_url(
            api_url,
            allow_insecure_loopback=allow_insecure_loopback,
        )
        self._timeout_seconds = timeout_seconds
        self._opener = request.build_opener(_NoRedirect())

    def _url(self, path: str, query: Mapping[str, str] | None) -> str:
        suffix = path
        if query:
            suffix += "?" + parse.urlencode(query)
        return self._api_url + suffix

    def _request(
        self,
        *,
        method: str,
        path: str,
        token: str,
        payload: Mapping[str, object] | None = None,
        query: Mapping[str, str] | None = None,
    ) -> object:
        if not token or len(token) > 8192 or not token.isprintable():
            raise GitHubApiError("GitHub API token is invalid")
        url = self._url(path, query)
        data = None if payload is None else json.dumps(payload, sort_keys=True).encode("utf-8")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "synapse-github-app/0.1",
            "X-GitHub-Api-Version": API_VERSION,
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        outbound = request.Request(url, data=data, headers=headers, method=method)
        try:
            response = cast(
                _ReadableResponse,
                self._opener.open(outbound, timeout=self._timeout_seconds),  # noqa: S310
            )
            try:
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) > MAX_RESPONSE_BYTES:
                    raise GitHubApiError("GitHub API response exceeds the byte limit")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            finally:
                response.close()
        except error.HTTPError as exc:
            raise GitHubApiError(
                f"GitHub API returned HTTP {exc.code} for {method} {path}"
            ) from None
        except (OSError, ValueError) as exc:
            raise GitHubApiError(f"GitHub API request failed for {method} {path}") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise GitHubApiError("GitHub API response exceeds the byte limit")
        try:
            return loads_strict_bounded(raw, max_depth=64)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise GitHubApiError("GitHub API returned invalid JSON") from exc

    def create_installation_token(self, installation_id: int, *, app_jwt: str) -> InstallationToken:
        """Exchange an App JWT for a least-privilege installation token."""
        if isinstance(installation_id, bool) or installation_id <= 0:
            raise GitHubApiError("installation id must be positive")
        response = self._request(
            method="POST",
            path=f"/app/installations/{installation_id}/access_tokens",
            token=app_jwt,
            payload={"permissions": {"checks": "write", "pull_requests": "read"}},
        )
        try:
            return parse_installation_token(response)
        except PayloadError as exc:
            raise GitHubApiError("GitHub API returned an invalid installation token") from exc

    def list_open_pull_requests(
        self, repository: Repository, *, token: str
    ) -> PullRequestInventory:
        """Read at most 100 open pull requests for one repository."""
        owner = parse.quote(repository.owner, safe="")
        name = parse.quote(repository.name, safe="")
        response = self._request(
            method="GET",
            path=f"/repos/{owner}/{name}/pulls",
            token=token,
            query={"state": "open", "per_page": str(PAGE_SIZE), "page": "1"},
        )
        values = _json_list(response, "pull requests")
        items = tuple(PullRequestSeed.from_api(value) for value in values)
        numbers = {item.number for item in items}
        if len(numbers) != len(items):
            raise GitHubApiError("GitHub API returned duplicate pull-request numbers")
        return PullRequestInventory(items=items, truncated=len(values) == MAX_OPEN_PULL_REQUESTS)

    def list_pull_files(
        self, repository: Repository, pull_number: int, *, token: str
    ) -> FileInventory:
        """Read up to GitHub's 3,000-file REST ceiling for one pull request."""
        if isinstance(pull_number, bool) or pull_number <= 0:
            raise GitHubApiError("pull-request number must be positive")
        owner = parse.quote(repository.owner, safe="")
        name = parse.quote(repository.name, safe="")
        paths: list[object] = []
        pages = MAX_FILES_PER_PULL_REQUEST // PAGE_SIZE
        for page in range(1, pages + 1):
            response = self._request(
                method="GET",
                path=f"/repos/{owner}/{name}/pulls/{pull_number}/files",
                token=token,
                query={"per_page": str(PAGE_SIZE), "page": str(page)},
            )
            values = _json_list(response, "pull-request files")
            for value in values:
                if not isinstance(value, dict):
                    raise GitHubApiError("GitHub API returned an invalid file record")
                paths.append(cast(Mapping[str, object], value).get("filename"))
            if len(values) < PAGE_SIZE:
                return FileInventory(paths=normalize_paths(paths), truncated=False)
        return FileInventory(paths=normalize_paths(paths), truncated=True)

    def create_check_run(
        self,
        repository: Repository,
        *,
        token: str,
        check: CheckRunRequest,
    ) -> int:
        """Create one completed neutral Check Run and return its id."""
        owner = parse.quote(repository.owner, safe="")
        name = parse.quote(repository.name, safe="")
        response = self._request(
            method="POST",
            path=f"/repos/{owner}/{name}/check-runs",
            token=token,
            payload=check.as_payload(),
        )
        if not isinstance(response, dict):
            raise GitHubApiError("GitHub API returned an invalid Check Run")
        check_id = cast(Mapping[str, object], response).get("id")
        if isinstance(check_id, bool) or not isinstance(check_id, int) or check_id <= 0:
            raise GitHubApiError("GitHub API returned an invalid Check Run id")
        return check_id
