# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — public metadata inspection boundaries
"""Exercise repository inspection decisions at the public functions."""

from __future__ import annotations

import base64
import io
import time
from collections.abc import Mapping
from email.message import Message
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request

import pytest
from tools import vendor_host_inspection as inspection

BLOB = ".github/AbCdEfGhIjKlMn"
WORKFLOW = ".github/workflows/update.yml"


class _Body:
    """One bounded response body, as urllib presents it to the caller."""

    def __init__(self, payload: bytes) -> None:
        """Store the bytes ``read`` will return."""
        self.payload = payload

    def read(self, limit: int) -> bytes:
        """Return at most ``limit`` bytes."""
        return self.payload[:limit]

    def __enter__(self) -> _Body:
        """Return this body for the ``with`` statement."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Close nothing; the body is already in memory."""


class _Transport:
    """Stand in for the socket after the URL has been accepted."""

    def __init__(self, payload: bytes = b"{}", error: BaseException | None = None) -> None:
        """Record requests and return ``payload`` or raise ``error``."""
        self.payload = payload
        self.error = error
        self.requests: list[Request] = []

    def open(self, request: Request, timeout: float | None = None) -> _Body:
        """Accept one prepared request."""
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return _Body(self.payload)


def _install(monkeypatch: pytest.MonkeyPatch, transport: _Transport) -> None:
    """Route accepted GitHub requests through ``transport``."""

    def build(handler: object) -> _Transport:
        if not isinstance(handler, inspection._NoRedirect):
            raise AssertionError("metadata reads must refuse redirects")
        return transport

    monkeypatch.setattr(inspection, "build_opener", build)


def _encoded(text: str) -> dict[str, str]:
    """Return one GitHub contents object."""
    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


def _host(name: str, *, language: str | None = "HTML", pages: bool = True) -> dict[str, Any]:
    """Return search metadata for one public repository."""
    owner, project = name.split("/")
    return {
        "full_name": name,
        "name": project,
        "owner": {"login": owner},
        "default_branch": "main",
        "has_pages": pages,
        "language": language,
        "license": None,
    }


def _lure_pages() -> dict[str, Any]:
    """Return the tree and text objects of one reviewed lure."""
    name = "owner/project"
    readme = "# project\nDownload Link: https://owner.github.io/project/\n"
    workflow = (
        "on:\n  schedule:\n    - cron: '*/5 * * * *'\n"
        "permissions:\n  contents: write\n"
        f"jobs:\n  update:\n    steps:\n      - run: git commit {BLOB}\n"
    )
    tree = {
        "truncated": False,
        "tree": [
            {"path": path, "type": "blob"}
            for path in ("README.md", "index.html", BLOB, WORKFLOW, "mark.svg")
        ],
    }
    return {
        inspection._api_url(f"/repos/{name}/git/trees/main") + "?recursive=1": tree,
        inspection._api_url(f"/repos/{name}/contents/README.md"): _encoded(readme),
        inspection._api_url(f"/repos/{name}/contents/{WORKFLOW}"): _encoded(workflow),
    }


def test_retry_keeps_broken_provenance_and_retries_a_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing or redirected object is not retried; a transient error is."""
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)

    def missing() -> dict[str, Any]:
        raise inspection.RepositoryMissing("absent")

    with pytest.raises(inspection.RepositoryMissing):
        inspection.retry_inspection(missing)
    assert slept == []

    def redirected() -> dict[str, Any]:
        raise inspection.RepositoryRedirected("moved")

    with pytest.raises(inspection.RepositoryRedirected):
        inspection.retry_inspection(redirected)
    assert slept == []

    attempts = {"count": 0}

    def transient() -> dict[str, Any]:
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise inspection.InspectionError("temporary")
        return {"ok": True}

    assert inspection.retry_inspection(transient) == {"ok": True}
    assert slept == [0.2]

    def always() -> dict[str, Any]:
        raise inspection.InspectionError("still unavailable")

    with pytest.raises(inspection.InspectionError, match="still unavailable"):
        inspection.retry_inspection(always)
    assert slept == [0.2, 0.2, 0.4]


def test_metadata_url_and_response_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a bounded JSON object from the fixed REST host is accepted."""
    refused = (
        "http://api.github.com/repos/a/b",
        "https://github.com/repos/a/b",
        "https://api.github.com/repos/a/b?x=1",
        "https://api.github.com/repos/a/b#frag",
        "https://api.github.com/orgs/a",
        "https://api.github.com/repos/../secret",
    )
    for url in refused:
        with pytest.raises(inspection.InspectionError):
            inspection.fetch_github_json(url)

    handler = inspection._NoRedirect()
    with pytest.raises(inspection.RepositoryRedirected, match="redirect refused"):
        handler.redirect_request(
            Request("https://api.github.com/repos/a/b"),
            None,
            302,
            "Found",
            Message(),
            "https://example.invalid/moved",
        )

    token = "fixture-token"
    monkeypatch.setenv("GITHUB_TOKEN", token)
    transport = _Transport(b'{"full_name": "a/b"}')
    _install(monkeypatch, transport)
    url = "https://api.github.com/repos/a/b?recursive=1"
    assert inspection.fetch_github_json(url) == {"full_name": "a/b"}
    assert transport.requests[0].get_header("Authorization") == f"Bearer {token}"
    monkeypatch.delenv("GITHUB_TOKEN")
    plain = _Transport(b'{"users": true}')
    _install(monkeypatch, plain)
    assert inspection.fetch_github_json("https://api.github.com/users/a") == {"users": True}

    def http_error(code: int) -> HTTPError:
        return HTTPError(
            "https://api.github.com/repos/a/b",
            code,
            "status",
            Message(),
            io.BytesIO(b""),
        )

    _install(monkeypatch, _Transport(error=http_error(404)))
    with pytest.raises(inspection.RepositoryMissing, match="absent"):
        inspection.fetch_github_json("https://api.github.com/repos/missing/repo")
    _install(monkeypatch, _Transport(error=http_error(503)))
    with pytest.raises(inspection.InspectionError, match="HTTP 503"):
        inspection.fetch_github_json("https://api.github.com/repos/a/b")
    _install(monkeypatch, _Transport(error=OSError("down")))
    with pytest.raises(inspection.InspectionError, match="request failed"):
        inspection.fetch_github_json("https://api.github.com/repos/a/b")
    _install(monkeypatch, _Transport(b"x" * (inspection.MAX_RESPONSE_BYTES + 1)))
    with pytest.raises(inspection.InspectionError, match="byte limit"):
        inspection.fetch_github_json("https://api.github.com/repos/a/b")
    _install(monkeypatch, _Transport(b"\xff"))
    with pytest.raises(inspection.InspectionError, match="JSON is invalid"):
        inspection.fetch_github_json("https://api.github.com/repos/a/b")
    _install(monkeypatch, _Transport(b"[]"))
    with pytest.raises(inspection.InspectionError, match="root is invalid"):
        inspection.fetch_github_json("https://api.github.com/repos/a/b")


def test_reachability_names_only_the_exact_declared_repository() -> None:
    """A renamed, missing, or non-GitHub URL does not count as provenance."""
    assert inspection.repository_reachability(None) == "not_declared"
    assert inspection.repository_reachability("http://github.com/a/b") == "unsupported_url"
    assert inspection.repository_reachability("https://gitlab.com/a/b") == "unsupported_url"
    assert inspection.repository_reachability("https://github.com/a/b?q=1") == "unsupported_url"
    assert inspection.repository_reachability("https://github.com/a/b#frag") == "unsupported_url"
    assert inspection.repository_reachability("https://github.com/.hidden/b") == "unsupported_url"
    assert inspection.repository_reachability("https://github.com/a/b.") == "unsupported_url"

    def unavailable(_url: str) -> Mapping[str, Any]:
        raise inspection.InspectionError("down")

    assert (
        inspection.repository_reachability("https://github.com/a/b", fetch=unavailable)
        == "unavailable"
    )
    assert (
        inspection.repository_reachability(
            "https://github.com/Owner/Name",
            fetch=lambda _url: {"full_name": "owner/name"},
        )
        == "reachable"
    )


def test_source_presence_and_lure_shape_fail_closed() -> None:
    """Source files outrank a lure, and a broken lure stays merely source-absent."""
    host = _host("owner/project")
    seen: list[str] = []

    def tree_only(url: str) -> dict[str, Any]:
        seen.append(url)
        return {
            "truncated": False,
            "tree": [
                {"path": ".github/hooks.py", "type": "blob"},
                {"path": "Cargo.toml", "type": "blob"},
                {"path": "README.md", "type": "blob"},
                "skip-me",
                {"type": "tree", "path": "src"},
                {"path": 12, "type": "blob"},
            ],
        }

    present = inspection.inspect_host(host, fetch=tree_only)
    assert present == {"code_file_count": 1, "inspection": "code_present"}
    assert "Cargo.toml" not in seen[0] or "git/trees" in seen[0]

    spaced = _host("owner/project")
    spaced["default_branch"] = "feature branch"
    seen.clear()

    def empty(url: str) -> dict[str, Any]:
        seen.append(url)
        return {"truncated": False, "tree": []}

    assert inspection.inspect_host(spaced, fetch=empty)["inspection"] == "source_absent"
    assert "feature%20branch" in seen[0]

    for branch in ("", "x" * 101, None):
        broken = _host("owner/project")
        broken["default_branch"] = branch
        with pytest.raises(inspection.InspectionError, match="default branch"):
            inspection.inspect_host(broken, fetch=empty)

    with pytest.raises(inspection.InspectionError, match="incomplete"):
        inspection.inspect_host(
            host,
            fetch=lambda _url: {"truncated": True, "tree": []},
        )
    with pytest.raises(inspection.InspectionError, match="incomplete"):
        inspection.inspect_host(host, fetch=lambda _url: {"truncated": False, "tree": {}})
    huge = [
        {"path": f"notes/n{index}.txt", "type": "blob"}
        for index in range(inspection.MAX_TREE_ENTRIES + 1)
    ]
    with pytest.raises(inspection.InspectionError, match="incomplete"):
        inspection.inspect_host(
            host,
            fetch=lambda _url: {"truncated": False, "tree": huge},
        )

    calls = {"count": 0}

    def tree(url: str) -> dict[str, Any]:
        calls["count"] += 1
        return {
            "truncated": False,
            "tree": [
                {"path": "README.md", "type": "blob"},
                {"path": "index.html", "type": "blob"},
                {"path": BLOB, "type": "blob"},
                {"path": WORKFLOW, "type": "blob"},
                {"path": "extra.txt", "type": "blob"},
            ],
        }

    assert inspection.inspect_host(host, fetch=tree)["inspection"] == "source_absent"
    assert calls["count"] == 1
    quiet = _host("owner/project", language="Python", pages=False)
    assert inspection.needs_lure_inspection(quiet) is False
    assert inspection.needs_lure_inspection(_host("owner/project", language=None)) is True


def test_exact_lure_is_rejected_and_a_partial_one_is_not() -> None:
    """The reviewed text pattern rejects the host; a missing phrase does not."""
    pages = _lure_pages()

    def fetch(url: str) -> dict[str, Any]:
        if url not in pages:
            raise inspection.InspectionError("unexpected metadata")
        return cast(dict[str, Any], pages[url])

    result = inspection.inspect_host(_host("owner/project"), fetch=fetch)
    assert result == {"code_file_count": 0, "inspection": "rejected_lure"}

    readme_url = inspection._api_url("/repos/owner/project/contents/README.md")
    pages[readme_url] = _encoded("# project\nNo download is published.\n")
    assert (
        inspection.inspect_host(_host("owner/project"), fetch=fetch)["inspection"]
        == "source_absent"
    )
    oversized = base64.b64encode(b"x" * (inspection.MAX_TEXT_BYTES + 1)).decode()
    pages[readme_url] = {"encoding": "base64", "content": oversized}
    with pytest.raises(inspection.InspectionError, match="text exceeded"):
        inspection.inspect_host(_host("owner/project"), fetch=fetch)
    pages[readme_url] = {"encoding": "utf-8", "content": "plain"}
    with pytest.raises(inspection.InspectionError, match="text response is invalid"):
        inspection.inspect_host(_host("owner/project"), fetch=fetch)
    pages[readme_url] = {"encoding": "base64", "content": "!!!!"}
    with pytest.raises(inspection.InspectionError, match="encoding is invalid"):
        inspection.inspect_host(_host("owner/project"), fetch=fetch)
    pages[readme_url] = {"encoding": "base64", "content": base64.b64encode(b"\xff").decode()}
    with pytest.raises(inspection.InspectionError, match="encoding is invalid"):
        inspection.inspect_host(_host("owner/project"), fetch=fetch)
    pages[readme_url] = {"encoding": "base64", "content": None}
    with pytest.raises(inspection.InspectionError, match="text response is invalid"):
        inspection.inspect_host(_host("owner/project"), fetch=fetch)


def test_owner_history_and_release_digest_are_bounded() -> None:
    """Owner age and a sha256 release asset are read; broken fields stay empty."""
    name = "owner/project"
    user_url = inspection._api_url("/users/owner")
    release_url = inspection._api_url(f"/repos/{name}/releases/latest")
    digest = "sha256:" + ("ab" * 32)
    pages = {
        user_url: {"created_at": "2020-01-01T00:00:00Z", "public_repos": 4},
        release_url: {
            "draft": False,
            "assets": [
                "skip",
                {"digest": "sha256:zz"},
                {"digest": digest},
            ],
        },
    }

    def fetch(url: str) -> Mapping[str, Any]:
        if url not in pages:
            raise inspection.RepositoryMissing("absent")
        return pages[url]

    result = inspection.host_provenance(_host(name), fetch=fetch)
    assert result["owner_public_repos"] == 4
    assert isinstance(result["owner_age_days"], int)
    assert result["owner_age_days"] >= 0
    assert result["release_digest_present"] is True

    pages[user_url] = {"created_at": "not-a-date", "public_repos": True}
    pages[release_url] = {"draft": True, "assets": [{"digest": digest}]}
    quiet = inspection.host_provenance(_host(name), fetch=fetch)
    assert quiet["owner_age_days"] is None
    assert quiet["owner_public_repos"] is None
    assert quiet["release_digest_present"] is False

    pages[user_url] = {"created_at": "2999-01-01T00:00:00Z", "public_repos": -1}
    pages[release_url] = {"draft": False, "assets": "none"}
    future = inspection.host_provenance(_host(name), fetch=fetch)
    assert future["owner_age_days"] == 0
    assert future["owner_public_repos"] is None
    assert future["release_digest_present"] is False

    def missing_user(url: str) -> Mapping[str, Any]:
        if url == user_url:
            raise inspection.RepositoryMissing("no user")
        return pages[release_url]

    absent_owner = inspection.host_provenance(_host(name), fetch=missing_user)
    assert absent_owner["owner_age_days"] is None
    assert absent_owner["release_digest_present"] is False

    def missing_release(url: str) -> Mapping[str, Any]:
        if url == release_url:
            raise inspection.RepositoryMissing("no release")
        return {"created_at": "2020-01-01T00:00:00Z", "public_repos": 1}

    no_release = inspection.host_provenance(_host(name), fetch=missing_release)
    assert no_release["owner_public_repos"] == 1
    assert no_release["release_digest_present"] is False
