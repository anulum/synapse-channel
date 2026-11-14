# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — one statement of what a dashboard start serves
"""``/capabilities.json``, each surface's absence and the start's lines come from one table."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from dashboard_helpers import _authorized_get, _feeds_server
from synapse_channel.dashboard_feed_serving import (
    OPTIONAL_SURFACES,
    capability_summary,
    configured_flags,
)


def test_every_surface_of_the_table_answers_its_own_absence_with_its_flag() -> None:
    server = _feeds_server()
    try:
        answers = {
            surface.name: _authorized_get(server, surface.path) for surface in OPTIONAL_SURFACES
        }
    finally:
        server.close()

    for surface in OPTIONAL_SURFACES:
        status, content_type, body = answers[surface.name]
        assert (status, content_type) == (404, "text/plain"), surface.path
        assert body == (
            f"{surface.name} feed not configured; start the dashboard with {surface.flag}\n"
        )


def test_capabilities_say_what_a_bare_start_serves(tmp_path: Path) -> None:
    server = _feeds_server()
    try:
        status, content_type, body = _authorized_get(server, "/capabilities.json")
        unauthenticated, _, _ = _authorized_get(server, "/capabilities.json", unauthenticated=True)
    finally:
        server.close()

    summary = json.loads(body)
    assert (status, content_type, unauthenticated) == (200, "application/json", 401)
    assert (summary["configured"], summary["total"]) == (0, len(OPTIONAL_SURFACES))
    assert summary["surfaces"][0] == {
        "name": "reliability",
        "path": "/reliability.json",
        "flag": "--feeds-db",
        "configured": False,
    }


@pytest.mark.parametrize("store", ["reliability_db", "federation_store", "attention_store"])
def test_capabilities_follow_the_stores_a_start_names(tmp_path: Path, store: str) -> None:
    from synapse_channel.dashboard import start_dashboard_server

    db = tmp_path / "store.db"
    sqlite3.connect(db).close()
    server = start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
        reliability_db=db if store == "reliability_db" else None,
        federation_store=db if store == "federation_store" else None,
        attention_store=db if store == "attention_store" else None,
    )
    try:
        _, _, body = _authorized_get(server, "/capabilities.json")
    finally:
        server.close()

    flag = {
        "reliability_db": "--feeds-db",
        "federation_store": "--federation-store",
        "attention_store": "--attention-store",
    }[store]
    summary = json.loads(body)
    expected = {s.name for s in OPTIONAL_SURFACES if s.flag == flag}
    served = {entry["name"] for entry in summary["surfaces"] if entry["configured"]}
    assert served == expected
    assert (summary["configured"], summary["total"]) == (len(expected), len(OPTIONAL_SURFACES))


def test_configured_flags_name_only_the_stores_given() -> None:
    assert configured_flags(feeds_db=None, federation_store=None, attention_store=None) == set()
    assert configured_flags(
        feeds_db=Path("hub.db"), federation_store=None, attention_store=Path("queue.db")
    ) == {"--feeds-db", "--attention-store"}
    summary = capability_summary(frozenset({"--federation-store"}))
    assert summary["configured"] == 1


def test_a_bare_start_names_every_missing_surface_by_its_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from test_dashboard_cli import _FakeDashboardServer, _run_dispatcher

    assert _run_dispatcher(monkeypatch, _FakeDashboardServer(token=None, generated=False)) == 0
    lines = capsys.readouterr().out.splitlines()

    assert "capabilities JSON: http://127.0.0.1:8765/capabilities.json" in lines
    store_feeds = ", ".join(s.name for s in OPTIONAL_SURFACES if s.flag == "--feeds-db")
    assert f"not served without --feeds-db: {store_feeds}" in lines
    assert "not served without --federation-store: federation" in lines
    assert "not served without --attention-store: attention" in lines
