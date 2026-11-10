# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse hub --metrics-token-set` in a real process
"""A real ``synapse hub`` rotates its metrics bearer from a token set and refuses bad starts."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import CliResult, free_port, http_get, isolated_hub, run_cli
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.token_audit import METRICS_SET_APPLIED_KIND, METRICS_SET_REJECTED_KIND
from synapse_channel.core.token_set import connect_token_digest


def _write(path: Path, *secrets: tuple[str, str], raw: str | None = None) -> None:
    """Replace the owner-only set file atomically with digests of ``(id, secret)`` pairs."""
    tokens = [
        {"id": token_id, "sha256": connect_token_digest(secret)} for token_id, secret in secrets
    ]
    staging = path.with_name(path.name + ".new")
    staging.write_text(raw or json.dumps({"version": 1, "tokens": tokens}), "utf-8")
    staging.chmod(0o600)
    os.replace(staging, path)


def _bearer(secret: str) -> dict[str, str]:
    """Return the Authorization header for ``secret``."""
    return {"Authorization": f"Bearer {secret}"}


def _audit_rows(db_path: Path) -> list[tuple[str, dict[str, Any]]]:
    """Read the metrics token-set audit rows a stopped hub left in its journal."""
    store = EventStore(db_path)
    try:
        kinds = (METRICS_SET_APPLIED_KIND, METRICS_SET_REJECTED_KIND)
        return [(event.kind, dict(event.payload)) for event in store.iter_events(kinds=kinds)]
    finally:
        store.close()


def test_a_hub_process_rotates_its_metrics_bearer_and_journals_each_set(tmp_path: Path) -> None:
    """Old token 200 → replaced file → old 401 / new 200; a bad file keeps the last good set."""
    tokens = tmp_path / "metrics-tokens.json"
    _write(tokens, ("scraper-1", "first-secret"))
    with isolated_hub(
        tmp_path, extra_args=["--metrics", "--metrics-token-set", str(tokens)]
    ) as hub:
        url = f"http://127.0.0.1:{hub.port}/metrics"
        assert http_get(url)[0] == 401
        status, body = http_get(url, headers=_bearer("first-secret"))
        assert status == 200 and "synapse_up" in body

        _write(tokens, ("scraper-2", "second-secret-longer"))
        assert http_get(url, headers=_bearer("first-secret"))[0] == 401
        assert http_get(url, headers=_bearer("second-secret-longer"))[0] == 200

        _write(tokens, raw="{not json")
        assert http_get(url, headers=_bearer("second-secret-longer"))[0] == 200
        db_path = hub.db_path
    rows = _audit_rows(db_path)
    assert [kind for kind, _ in rows] == [
        METRICS_SET_APPLIED_KIND,
        METRICS_SET_APPLIED_KIND,
        METRICS_SET_REJECTED_KIND,
    ]
    assert [payload.get("token_ids") for _, payload in rows[:2]] == [["scraper-1"], ["scraper-2"]]
    assert "first-secret" not in json.dumps(rows) and "second-secret" not in json.dumps(rows)


def _refused_start(tmp_path: Path, *extra: str) -> CliResult:
    """Start ``synapse hub --metrics`` with ``extra``; prove no listener, store or pins remain."""
    port = free_port()
    db_path = tmp_path / "refused.db"
    pins = tmp_path / "refused-pins.json"
    result = run_cli(
        "hub",
        "--port",
        str(port),
        "--db",
        str(db_path),
        "--identity-pins",
        str(pins),
        "--metrics",
        *extra,
    )
    assert result.returncode != 0, result.output
    assert not db_path.exists() and not pins.exists()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", port))
    return result


def test_a_missing_token_set_file_refuses_startup(tmp_path: Path) -> None:
    """A missing set file is named in the refusal."""
    result = _refused_start(tmp_path, "--metrics-token-set", str(tmp_path / "absent.json"))
    assert "absent.json" in result.stderr


def test_a_malformed_token_set_file_refuses_startup(tmp_path: Path) -> None:
    """A set file that is not a token set is refused before the hub listens."""
    tokens = tmp_path / "metrics-tokens.json"
    _write(tokens, raw='{"version": 1}')
    result = _refused_start(tmp_path, "--metrics-token-set", str(tokens))
    assert "synapse hub:" in result.stderr and "version and tokens" in result.stderr


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits define a group-readable file")
def test_a_group_readable_token_set_file_refuses_startup(tmp_path: Path) -> None:
    """A set file readable by others is refused as unsafe."""
    tokens = tmp_path / "metrics-tokens.json"
    _write(tokens, ("scraper", "secret"))
    tokens.chmod(0o640)
    result = _refused_start(tmp_path, "--metrics-token-set", str(tokens))
    assert "metrics-tokens.json" in result.stderr


def test_a_fixed_metrics_token_beside_the_set_refuses_startup(tmp_path: Path) -> None:
    """Both fixed sources, the flag and the file, are refused beside a set."""
    tokens = tmp_path / "metrics-tokens.json"
    _write(tokens, ("scraper", "secret"))
    fixed_file = tmp_path / "metrics-token.txt"
    fixed_file.write_text("fixed-secret\n", "utf-8")
    fixed_file.chmod(0o600)
    for fixed in (("--metrics-token", "fixed-secret"), ("--metrics-token-file", str(fixed_file))):
        result = _refused_start(tmp_path, "--metrics-token-set", str(tokens), *fixed)
        assert "cannot be combined with --metrics-token" in result.stderr


def test_the_hardening_presets_count_the_set_as_metrics_bearer_auth(tmp_path: Path) -> None:
    """At the real entry, ``--secure`` and ``--paranoid`` accept the set for the metrics surface."""
    tokens = tmp_path / "metrics-tokens.json"
    _write(tokens, ("scraper", "secret"))
    metrics_line = "--metrics-token, --metrics-token-file or --metrics-token-set"

    without_set = _refused_start(tmp_path, "--secure")
    with_set = _refused_start(tmp_path, "--secure", "--metrics-token-set", str(tokens))
    assert metrics_line in without_set.stderr
    assert "secure mode requires" in with_set.stderr and metrics_line not in with_set.stderr

    paranoid = ("--paranoid", "--token", "connect-secret")
    without_set = _refused_start(tmp_path, *paranoid)
    with_set = _refused_start(tmp_path, *paranoid, "--metrics-token-set", str(tokens))
    assert "requires --metrics-token or --metrics-token-set" in without_set.stderr
    assert "requires --message-auth-key" in with_set.stderr
