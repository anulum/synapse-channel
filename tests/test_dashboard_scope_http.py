# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — scopes through the production dashboard server
"""Serve the snapshot, the Studio projection and the live channel in a scope."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

import synapse_channel.dashboard as dashboard_module
from dashboard_helpers import _feeds_server
from dashboard_scope_fleet import scope_fleet
from synapse_channel.dashboard import DashboardServer, DashboardSnapshot
from synapse_channel.dashboard_live_transport import snapshot_fingerprint

TOKEN = "scope-snapshot-secret"


async def _fetch(**_kwargs: object) -> DashboardSnapshot:
    """Answer every collection with the same seeded fleet."""
    return scope_fleet()


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[DashboardServer]:
    monkeypatch.setattr(dashboard_module, "fetch_dashboard_snapshot", _fetch)
    running = _feeds_server(dashboard_token=TOKEN)
    try:
        yield running
    finally:
        running.close()


def _get(server: DashboardServer, path: str) -> tuple[int, dict[str, str], bytes]:
    request = Request(
        server.url(path), headers={"Connection": "close", "Authorization": f"Bearer {TOKEN}"}
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _snapshot(server: DashboardServer, scope: str | None) -> tuple[dict[str, str], bytes]:
    path = "/snapshot.json" if scope is None else f"/snapshot.json?scope={quote(scope)}"
    status, headers, body = _get(server, path)
    assert status == 200, body
    return headers, body


def test_a_project_scope_serves_only_its_part_with_its_own_tag(server: DashboardServer) -> None:
    whole_headers, whole_body = _snapshot(server, None)
    headers, body = _snapshot(server, "project:ALPHA")
    document = json.loads(body)

    assert [task["task_id"] for task in document["board"]["tasks"]] == ["A1"]
    assert document["online_agents"] == ["ALPHA/worker", "ALPHA/reviewer"]
    assert (document["scope"]["scope"], document["scope"]["applied"]) == ("project:ALPHA", True)
    assert (document["scope"]["tasks_in_scope"], document["scope"]["tasks_total"]) == (2, 5)
    assert headers["ETag"] == f'W/"{snapshot_fingerprint(document)}"' != whole_headers["ETag"]
    assert len(body) < len(whole_body)
    assert json.loads(whole_body)["scope"]["applied"] is False


def test_the_none_scope_and_the_union_rule_for_claims(server: DashboardServer) -> None:
    _, none_body = _snapshot(server, "none")
    _, beta_body = _snapshot(server, "project:BETA")
    unscoped, beta = json.loads(none_body), json.loads(beta_body)

    assert [task["task_id"] for task in unscoped["board"]["tasks"]] == ["N1", "N2"]
    assert unscoped["online_agents"] == ["solo"]
    assert [claim["owner"] for claim in beta["state"]["active_claims"]] == ["user/terminal-1"]


@pytest.mark.parametrize("query", ["scope=group:", "scope=project:", "scope=all&scope=none"])
@pytest.mark.parametrize("path", ["/snapshot.json", "/studio.json"])
def test_a_malformed_scope_is_refused_before_any_collection(
    server: DashboardServer, path: str, query: str
) -> None:
    status, _, body = _get(server, f"{path}?{query}")

    assert status == 400
    assert b"all, none, project:<namespace>, group:<name> or peer:<hub id>" in body


def test_the_studio_projection_is_built_in_the_scope(server: DashboardServer) -> None:
    status, _, body = _get(server, "/studio.json?scope=project%3AALPHA")
    studio = json.loads(body)

    assert status == 200
    assert studio["scope"]["scope"] == "project:ALPHA"
    assert studio["agents"]["live"] == ["ALPHA/reviewer", "ALPHA/worker"]
    assert [claim["owner"] for claim in studio["claims"]["active"]] == ["ALPHA/worker"]


def test_the_live_channel_sends_the_same_scoped_document(
    server: DashboardServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    _, body = _snapshot(server, "project:BETA")
    status, _, stream = _get(server, "/live.ndjson?cycles=1&scope=project%3ABETA")
    frames = [json.loads(line) for line in stream.splitlines()]
    snapshot = next(frame for frame in frames if frame.get("channel") == "snapshot")

    page = json.loads(body)
    assert status == 200
    # The two renders differ only in their render time, which the tag leaves out.
    assert snapshot_fingerprint(snapshot["data"]) == snapshot_fingerprint(page)
    assert snapshot["data"]["scope"] == page["scope"]
    assert snapshot["data"]["board"]["tasks"] == page["board"]["tasks"]
    refused, _, detail = _get(server, "/live.ndjson?cycles=1&scope=group%3Ainfra")
    assert refused == 400 and b"start the dashboard with --scope-groups" in detail


GROUPS = {"version": 1, "groups": {"pair": ["ALPHA", "BETA"], "beta": ["BETA"]}}


@pytest.fixture
def grouped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[DashboardServer]:
    monkeypatch.setattr(dashboard_module, "fetch_dashboard_snapshot", _fetch)
    groups = tmp_path / "groups.json"
    groups.write_text(json.dumps(GROUPS))
    groups.chmod(0o600)
    running = _feeds_server(dashboard_token=TOKEN, scope_groups_file=groups)
    try:
        yield running
    finally:
        running.close()


@pytest.mark.parametrize("path", ["/snapshot.json", "/studio.json"])
def test_a_group_scope_without_a_groups_file_is_refused_naming_the_flag(
    server: DashboardServer, path: str
) -> None:
    status, _, body = _get(server, f"{path}?scope=group%3Apair")

    assert status == 400
    assert body == b"group scopes need a groups file: start the dashboard with --scope-groups\n"


def test_a_declared_group_scope_serves_the_union_of_its_namespaces(
    grouped: DashboardServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    _, body = _snapshot(grouped, "group:pair")
    document = json.loads(body)
    status, _, studio = _get(grouped, "/studio.json?scope=group%3Apair")
    _, _, stream = _get(grouped, "/live.ndjson?cycles=1&scope=group%3Apair")
    live = next(
        frame
        for frame in map(json.loads, stream.splitlines())
        if frame.get("channel") == "snapshot"
    )

    assert [task["task_id"] for task in document["board"]["tasks"]] == ["A1", "B1"]
    assert (document["scope"]["scope"], document["scope"]["tasks_in_scope"]) == ("group:pair", 3)
    assert [group["group"] for group in document["scope"]["groups"]] == ["beta", "pair"]
    assert status == 200 and json.loads(studio)["scope"]["scope"] == "group:pair"
    assert snapshot_fingerprint(live["data"]) == snapshot_fingerprint(document)
    unknown, _, detail = _get(grouped, "/snapshot.json?scope=group%3Ainfra")
    assert (unknown, detail) == (400, b"this dashboard declares no group of that name\n")


def test_the_capabilities_say_whether_groups_are_declared(
    server: DashboardServer, grouped: DashboardServer
) -> None:
    bare = json.loads(_get(server, "/capabilities.json")[2])
    declared = json.loads(_get(grouped, "/capabilities.json")[2])

    assert bare["scope_groups"] == {"flag": "--scope-groups", "configured": False}
    assert declared["scope_groups"] == {"flag": "--scope-groups", "configured": True}
    assert declared["total"] == bare["total"]


def test_a_start_with_an_unusable_groups_file_is_refused(tmp_path: Path) -> None:
    groups = tmp_path / "groups.json"
    groups.write_text(json.dumps({"version": 1, "groups": {"empty": []}}))
    groups.chmod(0o600)

    with pytest.raises(ValueError, match="--scope-groups: group 'empty' needs a list"):
        _feeds_server(scope_groups_file=groups)
