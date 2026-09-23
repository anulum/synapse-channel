# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — public vendor and provider discovery contracts
"""Exercise the public discovery CLI and state transitions across feed boundaries."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from urllib.request import Request

import pytest
import tools.vendor_discovery as discovery


def _model(name: str) -> dict[str, object]:
    """Build one public model-provider record without an actual API call."""
    return {
        "id": name,
        "name": name.title(),
        "doc": f"https://{name}.example/docs",
        "api": f"https://{name}.example/v1",
        "npm": "@ai-sdk/openai-compatible",
        "env": ["API_KEY"],
        "models": {"one": {}},
    }


def _mcp(name: str, *, status: str = "active") -> dict[str, object]:
    """Build an official registry response entry with a declared transport."""
    return {
        "server": {
            "name": name,
            "title": name,
            "version": "1.0.0",
            "remotes": [{"type": "streamable-http", "url": "https://example.com/mcp"}],
        },
        "_meta": {
            "io.modelcontextprotocol.registry/official": {
                "status": status,
                "updatedAt": "2026-09-19T12:00:00Z",
            }
        },
    }


def _fixture(
    *,
    model_names: tuple[str, ...] = ("acme",),
    mcp_names: tuple[str, ...] = ("acme/tool",),
    host_names: tuple[str, ...] = ("acme/agent",),
    host_total: int | None = None,
) -> dict[str, Any]:
    """Supply all three source document shapes to the same CLI intake path."""
    return {
        "models_dev": {name: _model(name) for name in model_names},
        "mcp_registry": [
            {"servers": [_mcp(name) for name in mcp_names], "metadata": {"count": len(mcp_names)}}
        ],
        "github_hosts": {
            "total_count": len(host_names) if host_total is None else host_total,
            "incomplete_results": False,
            "items": [
                {
                    "full_name": name,
                    "name": name.split("/")[1],
                    "owner": {"login": name.split("/")[0]},
                    "html_url": f"https://github.com/{name}",
                    "updated_at": "2026-09-19T12:00:00Z",
                    "default_branch": "main",
                    "license": {"spdx_id": "MIT"},
                }
                for name in host_names
            ],
        },
        "github_api": {
            f"https://api.github.com/repos/{name}/git/trees/main?recursive=1": {
                "tree": [{"path": "src/agent.py", "type": "blob"}],
                "truncated": False,
            }
            for name in host_names
        }
        | {
            f"https://api.github.com/users/{name.split('/')[0]}": {
                "created_at": "2020-01-01T00:00:00Z",
                "public_repos": 4,
            }
            for name in host_names
        }
        | {
            f"https://api.github.com/repos/{name}/releases/latest": {"draft": False, "assets": []}
            for name in host_names
        },
    }


def _scan(
    fixture: dict[str, Any], now: datetime
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, bool]]:
    """Run production collection using only injected feed bytes."""
    rows, errors, complete = discovery.collect(fixture=fixture, now=now)
    assert errors == {}
    return rows, complete


def test_real_cli_fixture_persists_first_and_last_seen(tmp_path: Path) -> None:
    """One operator command writes an inspectable report and separate next catalog."""
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(_fixture()), encoding="utf-8")
    catalog_path = tmp_path / "initial.json"
    catalog_path.write_text(
        '{"schema":1,"checked_at":null,"sources":{},"candidates":{}}', encoding="utf-8"
    )
    report = tmp_path / "report.json"
    next_catalog = tmp_path / "next.json"
    result = subprocess.run(
        [
            sys.executable,
            "tools/vendor_discovery.py",
            "--catalog",
            str(catalog_path),
            "--fixture",
            str(fixture_path),
            "--report",
            str(report),
            "--next-catalog",
            str(next_catalog),
            "--strict",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    recorded = json.loads(report.read_text(encoding="utf-8"))
    assert recorded["counts"] == {"observed": 3, "review_queue": 3, "persisted": 3}
    assert recorded["review_queue"][0]["evidence"][0]["claims"]["interface"] is None
    assert {item["suggested_lane"] for item in recorded["review_queue"]} == {
        "C07",
        "C09/C15",
        "C15",
    }
    state = json.loads(next_catalog.read_text(encoding="utf-8"))
    assert state["candidates"]["provider:acme"]["first_seen"] == state["checked_at"]
    assert (
        "provider:acme"
        in state["candidates"]["provider:acme"]["sources"][discovery.MODEL_URL]["items"]
    )


def test_alias_duplicate_rename_publisher_conflict_and_reappearance() -> None:
    """An alias never hides a changed publisher and a withdrawn item can return."""
    config, catalog = discovery.load_inputs(discovery.DEFAULT_CONFIG, discovery.DEFAULT_CATALOG)
    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    rows, complete = _scan(_fixture(mcp_names=("acme/old",)), now)
    first, catalog = discovery.reconcile(config, catalog, rows, {}, complete, now=now)
    assert first["counts"]["observed"] == 3
    config["aliases"] = {"mcp:acme/new": "mcp:acme/old"}
    later = datetime(2026, 9, 20, tzinfo=timezone.utc)
    rows, complete = _scan(_fixture(mcp_names=("acme/new",)), later)
    report, renamed = discovery.reconcile(config, catalog, rows, {}, complete, now=later)
    assert report["counts"]["observed"] == 3
    assert (
        renamed["candidates"]["mcp:acme/old"]["first_seen"]
        == catalog["candidates"]["mcp:acme/old"]["first_seen"]
    )
    assert (
        "mcp:acme/new"
        in renamed["candidates"]["mcp:acme/old"]["sources"][discovery.MCP_URL]["items"]
    )
    rows, complete = _scan(_fixture(mcp_names=("acme/old", "acme/new")), later)
    _, duplicate = discovery.reconcile(config, catalog, rows, {}, complete, now=later)
    assert set(duplicate["candidates"]["mcp:acme/old"]["sources"][discovery.MCP_URL]["items"]) == {
        "mcp:acme/old",
        "mcp:acme/new",
    }
    config["aliases"]["mcp:other/new"] = "mcp:acme/old"
    rows, complete = _scan(_fixture(mcp_names=("other/new",)), later)
    conflict, _ = discovery.reconcile(config, catalog, rows, {}, complete, now=later)
    assert any(item["status"] == "publisher_conflict" for item in conflict["review_queue"])

    rows, complete = _scan(_fixture(model_names=()), later)
    withdrawn, state = discovery.reconcile(config, catalog, rows, {}, complete, now=later)
    assert state["candidates"]["provider:acme"]["status"] == "withdrawn"
    assert any(item["key"] == "provider:acme" for item in withdrawn["review_queue"])
    rows, complete = _scan(_fixture(), later)
    returned, state = discovery.reconcile(config, state, rows, {}, complete, now=later)
    assert state["candidates"]["provider:acme"]["status"] == "reappeared"
    assert any(item["status"] == "reappeared" for item in returned["review_queue"])


def test_partial_source_never_proves_removal_and_deleted_status_does() -> None:
    """A bounded search page is incomplete; the registry's explicit status is evidence."""
    config, catalog = discovery.load_inputs(discovery.DEFAULT_CONFIG, discovery.DEFAULT_CATALOG)
    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    rows, complete = _scan(_fixture(host_total=1000), now)
    _, catalog = discovery.reconcile(config, catalog, rows, {}, complete, now=now)
    rows, complete = _scan(_fixture(host_names=(), host_total=1000), now)
    report, state = discovery.reconcile(config, catalog, rows, {}, complete, now=now)
    assert report["sources"]["github_hosts"]["status"] == "partial"
    assert state["candidates"]["host:acme/agent"]["status"] != "withdrawn"

    fixture = _fixture()
    fixture["mcp_registry"] = [
        {"servers": [_mcp("acme/tool", status="deleted")], "metadata": {"count": 1}}
    ]
    rows, complete = _scan(fixture, now)
    report, state = discovery.reconcile(config, catalog, rows, {}, complete, now=now)
    assert state["candidates"]["mcp:acme/tool"]["status"] == "withdrawn"
    assert any(item["status"] == "withdrawn" for item in report["review_queue"])
    again, _ = discovery.reconcile(config, state, rows, {}, complete, now=now)
    assert not any(item["key"] == "mcp:acme/tool" for item in again["review_queue"])


def test_bad_source_staleness_and_catalog_validation(tmp_path: Path) -> None:
    """Malformed feed and an expired source become visible failures, not empty lists."""
    config, catalog = discovery.load_inputs(discovery.DEFAULT_CONFIG, discovery.DEFAULT_CATALOG)
    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    fixture = _fixture()
    fixture["models_dev"] = {"bad": {"id": "wrong", "models": {}}}
    rows, errors, complete = discovery.collect(fixture=fixture, now=now)
    assert errors["models_dev"] == "DiscoveryError"
    catalog["sources"]["models_dev"] = {"last_successful_at": "2026-09-01T00:00:00+00:00"}
    report, _ = discovery.reconcile(config, catalog, rows, errors, complete, now=now)
    assert report["sources"]["models_dev"]["status"] == "stale"
    assert report["review_overdue"] is False
    config_path = tmp_path / "config.json"
    catalog_path = tmp_path / "catalog.json"
    config["aliases"] = {"bad": "provider:acme"}
    config_path.write_text(json.dumps(config), encoding="utf-8")
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(discovery.DiscoveryError):
        discovery.load_inputs(config_path, catalog_path)


class _Response:
    """Exercise the actual HTTP boundary without contacting the public internet."""

    def __init__(self, url: str, body: bytes):
        self.url = url
        self.body = body

    def __enter__(self) -> _Response:
        """Enter the response context."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Leave the response context."""

    def geturl(self) -> str:
        """Return the final URL."""
        return self.url

    def read(self, size: int) -> bytes:
        """Return a bounded HTTP body."""
        return self.body[:size]


def test_fetch_rejects_unapproved_query_redirect_and_oversize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feed input cannot turn a candidate URL into an arbitrary network request."""
    with pytest.raises(discovery.DiscoveryError):
        discovery.fetch_json("http://127.0.0.1/secret")
    with pytest.raises(discovery.DiscoveryError):
        discovery.fetch_json(discovery.MCP_URL + "?url=http://127.0.0.1")
    with pytest.raises(discovery.DiscoveryError):
        discovery.fetch_json(discovery.HOST_URL + "?q=unrestricted")
    with pytest.raises(discovery.DiscoveryError):
        discovery.fetch_json(discovery.MODEL_URL + "?foo=bar")
    seen: list[tuple[Request, int]] = []

    def open_ok(request: Request, timeout: int) -> _Response:
        seen.append((request, timeout))
        return _Response(discovery.MODEL_URL, b"{}")

    monkeypatch.setattr(discovery, "urlopen", open_ok)
    assert discovery.fetch_json(discovery.MODEL_URL) == {}
    assert seen[0][1] == 15
    monkeypatch.setattr(
        discovery, "urlopen", lambda request, timeout: _Response("https://evil.example", b"{}")
    )
    with pytest.raises(discovery.DiscoveryError):
        discovery.fetch_json(discovery.MODEL_URL)

    monkeypatch.setattr(
        discovery,
        "urlopen",
        lambda request, timeout: _Response(
            discovery.MODEL_URL, b"x" * (discovery.MAX_BYTES[discovery.MODEL_URL] + 1)
        ),
    )
    with pytest.raises(discovery.DiscoveryError):
        discovery.fetch_json(discovery.MODEL_URL)


def test_cli_refuses_overwrite_and_fails_strict_on_unavailable_feed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production entrypoint writes two outputs and protects its inputs."""
    fixture_path = tmp_path / "feed.json"
    fixture_path.write_text(json.dumps(_fixture()), encoding="utf-8")
    catalog_path = tmp_path / "initial.json"
    catalog_path.write_text(
        '{"schema":1,"checked_at":null,"sources":{},"candidates":{}}', encoding="utf-8"
    )
    report_path = tmp_path / "report.json"
    state_path = tmp_path / "state.json"
    args = [
        "discovery",
        "--catalog",
        str(catalog_path),
        "--fixture",
        str(fixture_path),
        "--report",
        str(report_path),
        "--next-catalog",
        str(state_path),
        "--strict",
    ]
    monkeypatch.setattr(sys, "argv", args)
    assert discovery.main() == 0
    assert json.loads(state_path.read_text(encoding="utf-8"))["candidates"]
    monkeypatch.setattr(
        sys, "argv", args[:-3] + ["--report", str(report_path), "--next-catalog", str(report_path)]
    )
    assert discovery.main() == 2

    bad = _fixture()
    bad["models_dev"] = []
    fixture_path.write_text(json.dumps(bad), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", args)
    assert discovery.main() == 1
    assert (
        json.loads(report_path.read_text(encoding="utf-8"))["sources"]["models_dev"]["status"]
        == "stale"
    )
    bad_path = tmp_path / "not-object.json"
    bad_path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "discovery",
            "--fixture",
            str(bad_path),
            "--report",
            str(report_path),
            "--next-catalog",
            str(state_path),
        ],
    )
    assert discovery.main() == 2


def test_registry_pagination_and_repeated_cursor_are_bounded() -> None:
    """An incremental registry cursor advances only within the page bound."""
    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    fixture = _fixture()
    fixture["mcp_registry"] = [
        {"servers": [_mcp("acme/one")], "metadata": {"nextCursor": "page2"}},
        {"servers": [_mcp("acme/two")], "metadata": {"nextCursor": "page3"}},
        {"servers": [_mcp("acme/three")], "metadata": {"nextCursor": "page4"}},
    ]
    rows, errors, complete = discovery.collect(fixture=fixture, now=now)
    assert errors == {}
    assert len(rows["mcp_registry"]) == 3
    assert complete["mcp_registry"] is False
    assert discovery._allowed_mcp_query(
        "limit=100&version=latest&include_deleted=true&updated_since=2026-09-12T00%3A00%3A00Z&cursor=page2"
    )
    fixture["mcp_registry"] = [
        {"servers": [], "metadata": {"nextCursor": "same"}},
        {"servers": [], "metadata": {"nextCursor": "same"}},
    ]
    rows, errors, complete = discovery.collect(fixture=fixture, now=now)
    assert errors["mcp_registry"] == "DiscoveryError"
    assert "mcp_registry" not in rows
    assert complete["mcp_registry"] is False


@pytest.mark.parametrize(
    "source,raw",
    [
        ("models", {"bad name": _model("bad name")}),
        ("models", {"bad": []}),
        ("mcp", {"servers": "bad", "metadata": {}}),
        ("mcp", {"servers": [], "metadata": {"nextCursor": 1}}),
        ("mcp", {"servers": ["bad"], "metadata": {}}),
        ("mcp", {"servers": [{"server": {}}], "metadata": {}}),
        ("mcp", {"servers": [{"server": {"name": "bad"}}], "metadata": {}}),
        ("mcp", {"servers": [{"server": {"name": "a/b"}, "_meta": []}], "metadata": {}}),
        (
            "mcp",
            {
                "servers": [
                    {
                        "server": {"name": "a/b"},
                        "_meta": {"io.modelcontextprotocol.registry/official": []},
                    }
                ],
                "metadata": {},
            },
        ),
        (
            "mcp",
            {
                "servers": [{"server": {"name": "a/b", "remotes": "bad"}, "_meta": {}}],
                "metadata": {},
            },
        ),
        ("hosts", {"items": "bad", "total_count": 0}),
        ("hosts", {"items": ["bad"], "total_count": 1}),
        ("hosts", {"items": [{"owner": {}}], "total_count": 1}),
    ],
)
def test_malformed_public_entries_are_rejected(source: str, raw: dict[str, object]) -> None:
    """Bad public metadata fails its feed instead of becoming a trusted empty list."""
    with pytest.raises(discovery.DiscoveryError):
        if source == "models":
            discovery.parse_models(raw)
        elif source == "mcp":
            discovery.parse_mcp(raw)
        else:
            discovery.parse_hosts(raw)


def test_live_fetch_path_uses_fixed_queries_and_rejects_bad_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The non-fixture collector constructs only the declared public URLs."""
    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    fixture = _fixture()
    requested: list[str] = []

    def fetch(url: str) -> dict[str, Any]:
        requested.append(url)
        if url == discovery.MODEL_URL:
            return cast(dict[str, Any], fixture["models_dev"])
        if url.startswith(discovery.HOST_URL):
            assert discovery._allowed_host_query(url.split("?", 1)[1])
            return cast(dict[str, Any], fixture["github_hosts"])
        assert url.startswith(discovery.MCP_URL)
        assert discovery._allowed_mcp_query(url.split("?", 1)[1])
        return cast(dict[str, Any], fixture["mcp_registry"][0])

    rows, errors, complete = discovery.collect(
        fetch=fetch,
        github_fetch=lambda url: cast(dict[str, Any], fixture["github_api"][url]),
        now=now,
    )
    assert errors == {}
    assert len(requested) == 4
    assert len(rows["models_dev"]) == 1 and complete["mcp_registry"] is True
    monkeypatch.setattr(
        discovery, "urlopen", lambda request, timeout: _Response(discovery.MODEL_URL, b"[]")
    )
    with pytest.raises(discovery.DiscoveryError):
        discovery.fetch_json(discovery.MODEL_URL)


@pytest.mark.parametrize(
    "config_change,catalog_change",
    [
        ({"schema": 2}, {}),
        ({"review_owner": ""}, {}),
        ({"reviewed_at": None}, {}),
        ({"review_interval_days": 0}, {}),
        ({"max_source_age_days": 99}, {}),
        ({}, {"schema": 2}),
        ({}, {"sources": []}),
        ({}, {"candidates": {"bad key": {}}}),
        ({}, {"candidates": {"provider:bad": []}}),
        ({}, {"candidates": {"provider:bad": {"publisher": "bad"}}}),
        (
            {},
            {
                "candidates": {
                    "provider:bad": {
                        "publisher": "bad",
                        "first_seen": "2026-09-19",
                        "sources": {"https://evil": {}},
                    }
                }
            },
        ),
        (
            {},
            {
                "candidates": {
                    "provider:bad": {
                        "publisher": "bad",
                        "first_seen": "2026-09-19",
                        "sources": {
                            discovery.MODEL_URL: {
                                "items": {"provider:bad": {"evidence_sha256": "bad"}},
                            }
                        },
                    }
                }
            },
        ),
    ],
)
def test_malformed_review_state_cannot_be_admitted(
    tmp_path: Path,
    config_change: dict[str, object],
    catalog_change: dict[str, object],
) -> None:
    """Corrupt committed review input fails before any public network access."""
    config, catalog = discovery.load_inputs(discovery.DEFAULT_CONFIG, discovery.DEFAULT_CATALOG)
    config.update(config_change)
    catalog.update(catalog_change)
    config_path = tmp_path / "config.json"
    catalog_path = tmp_path / "catalog.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises((ValueError, TypeError)):
        discovery.load_inputs(config_path, catalog_path)


def test_page_and_candidate_bounds_are_fail_closed() -> None:
    """Overlarge logical feeds and malformed page shape cannot extend a scan."""
    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    fixture = _fixture()
    fixture["mcp_registry"] = "not pages"
    _, errors, _ = discovery.collect(fixture=fixture, now=now)
    assert errors["mcp_registry"] == "DiscoveryError"
    fixture["mcp_registry"] = ["not a page"]
    _, errors, _ = discovery.collect(fixture=fixture, now=now)
    assert errors["mcp_registry"] == "DiscoveryError"
    with pytest.raises(discovery.DiscoveryError):
        discovery.parse_models({f"vendor-{i}": _model(f"vendor-{i}") for i in range(1001)})


def test_query_contract_rejects_broadened_registry_reads() -> None:
    """A cursor cannot add arbitrary parameters or widen a source query."""
    base = "limit=100&version=latest&include_deleted=true&updated_since=2026-09-12T00%3A00%3A00Z"
    assert not discovery._allowed_mcp_query(base + "&other=1")
    assert not discovery._allowed_mcp_query(base.replace("limit=100", "limit=1000"))
    assert not discovery._allowed_mcp_query(
        base.replace("include_deleted=true", "include_deleted=false")
    )
    assert not discovery._allowed_mcp_query(base.replace("2026-09-12", "yesterday"))
    assert not discovery._allowed_mcp_query(base + "&cursor=" + "a" * 301)


def test_public_catalog_excludes_unreviewed_host_names() -> None:
    """Raw search hits cannot become a versioned public host list."""
    _, catalog = discovery.load_inputs(discovery.DEFAULT_CONFIG, discovery.DEFAULT_CATALOG)
    assert not [
        key
        for key, row in catalog["candidates"].items()
        if row["kind"] == "host" and row["status"] in {"new", "changed", "seen"}
    ]
