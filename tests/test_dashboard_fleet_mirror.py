# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — live HTTP mirror disclosure regressions
"""Use real owner-only files and Core HTTP, without importing Fleet."""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from synapse_channel import dashboard_fleet_mirror
from synapse_channel.dashboard import DashboardServer, start_dashboard_server
from synapse_channel.dashboard_fleet_mirror import load_mirror_grants
from synapse_channel.fleet_mirror_contract import parse_mirror
from test_fleet_mirror_contract import document

TOKEN = "disposable-fleet-mirror-test"


def serve(export: Path | None = None, grants: Path | None = None) -> DashboardServer:
    """Start an isolated Core server; hub access is unnecessary for this feed."""
    return start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="mirror-test",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=2,
        allow_non_loopback=False,
        dashboard_token=TOKEN,
        fleet_observed_file=export,
        fleet_observed_access_file=grants,
    )


def request(
    server: DashboardServer, token: str | None = TOKEN, path: str = "/fleet-observed.json"
) -> tuple[int, bytes]:
    """Read one real bounded HTTP response."""
    try:
        response = urlopen(
            Request(
                server.url(path), headers=({"Authorization": f"Bearer {token}"} if token else {})
            ),
            timeout=5,
        )
    except HTTPError as exc:
        response = exc
    with response:
        assert response.headers.get("Cache-Control") == "no-store"
        return response.status, response.read()


def write(path: Path, data: object) -> None:
    """Persist a real owner-only JSON input."""
    path.write_text(json.dumps(data))
    path.chmod(0o600)


def test_live_http_access_and_revocation(tmp_path: Path) -> None:
    export, grants = tmp_path / "mirror.json", tmp_path / "grants.json"
    write(export, document())
    write(grants, {"version": 1, "observers": ["dashboard"]})
    server = serve(export, grants)
    try:
        assert request(server, None)[0] == 401
        assert request(server)[0] == 403
        # Compatibility bearer identity is explicit, not inferred from its role.
        from synapse_channel.dashboard_access import compatibility_access_policy

        principal = compatibility_access_policy(
            dashboard_token=TOKEN,
            token_protects_reads=True,
            operator_armed=False,
            operator_name="",
        ).resolve_credential(f"Bearer {TOKEN}")
        assert principal is not None
        write(grants, {"version": 1, "observers": [principal.principal_id]})
        code, raw = request(server)
        assert code == 200
        assert json.loads(raw) == document()
        assert request(server, path="/fleet-observed-access.json")[0] == 200
        doc = document()
        doc["version"] = 2
        write(export, doc)
        assert request(server)[0] == 409
        write(export, {})
        assert request(server)[0] == 503
        export.unlink()
        assert request(server)[0] == 503
        write(grants, {"version": 1, "observers": []})
        assert request(server)[0] == 403
        write(grants, {})
        assert request(server)[0] == 403
    finally:
        server.close()


def test_disabled_and_invalid_configuration(tmp_path: Path) -> None:
    server = serve()
    try:
        assert request(server)[0] == 404
    finally:
        server.close()
    with pytest.raises(ValueError, match="together"):
        serve(tmp_path / "mirror.json")


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"version": True, "observers": []},
        {"version": 1, "observers": {}},
        {"version": 1, "observers": ["bad principal"]},
        {"version": 1, "observers": ["a", "a"]},
    ],
)
def test_invalid_grants(tmp_path: Path, data: object) -> None:
    path = tmp_path / "grants.json"
    write(path, data)
    with pytest.raises(ValueError):
        load_mirror_grants(path)


@pytest.mark.parametrize(
    "replacement",
    [
        {"version": 1, "observers": []},
        {},
    ],
)
def test_revocation_during_real_export_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: dict[str, Any]
) -> None:
    export, grants = tmp_path / "mirror.json", tmp_path / "grants.json"
    write(export, document())
    write(grants, {"version": 1, "observers": ["compatibility"]})

    def revoke_after_parse(raw: str) -> dict[str, Any]:
        result = parse_mirror(raw)
        write(grants, replacement)
        return result

    monkeypatch.setattr(dashboard_fleet_mirror, "parse_mirror", revoke_after_parse)
    server = serve(export, grants)
    try:
        code, raw = request(server)
        assert code == 403
        assert b"hub-b" not in raw and b"task" not in raw
    finally:
        server.close()


def test_duplicate_policy_fields_refused(tmp_path: Path) -> None:
    path = tmp_path / "grants.json"
    path.write_text('{"version":1,"observers":[],"observers":["compatibility"]}')
    path.chmod(0o600)
    with pytest.raises(ValueError, match="duplicate"):
        load_mirror_grants(path)


def test_atomic_replacement_with_concurrent_http_readers(tmp_path: Path) -> None:
    export, grants = tmp_path / "mirror.json", tmp_path / "grants.json"
    first = document()
    second = document()
    second["exported_at"] = 200.0
    second["snapshot"]["generated_at"] = 199.0
    second["snapshot"]["peers"][0].update(
        cursor=22,
        events=22,
        last_success_at=198.0,
        consecutive_failures=0,
        status_written_at=199.0,
        caught_up=True,
    )
    second["snapshot"]["tasks"][0].update(status="done", title="second export")
    documents = (first, second)
    expected = {json.dumps(doc, sort_keys=True) for doc in documents}
    write(export, first)
    write(grants, {"version": 1, "observers": ["compatibility"]})
    server = serve(export, grants)
    barrier = threading.Barrier(5, timeout=5)
    rounds = 16

    def publish() -> None:
        try:
            for index in range(rounds):
                replacement = tmp_path / f"replacement-{index}.json"
                write(replacement, documents[index % 2])
                barrier.wait()
                os.replace(replacement, export)
                barrier.wait()
        except BaseException:
            barrier.abort()
            raise

    def read() -> int:
        successes = 0
        try:
            for _ in range(rounds):
                barrier.wait()
                code, raw = request(server)
                assert code in (200, 503)
                if code == 200:
                    assert json.dumps(json.loads(raw), sort_keys=True) in expected
                    successes += 1
                else:
                    assert raw == b"mirror unavailable"
                barrier.wait()
        except BaseException:
            barrier.abort()
            raise
        return successes

    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            writer = pool.submit(publish)
            readers = [pool.submit(read) for _ in range(4)]
            assert all(future.result(timeout=30) > 0 for future in readers)
            writer.result(timeout=5)
        code, raw = request(server)
        assert code == 200
        assert json.loads(raw) == second
    finally:
        barrier.abort()
        server.close()
