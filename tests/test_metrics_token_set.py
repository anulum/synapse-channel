# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the metrics bearer can follow a token set and rotate without a restart
"""A metrics token set admits valid entries only, rotates live, and is guarded and audited."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request

from hub_e2e_helpers import http_get, running_hub
from synapse_channel.cli_hub_startup import HubStartupRefused
from synapse_channel.cli_hub_transport import _metrics_token_set
from synapse_channel.cli_processes_security_args import add_hub_admission_arguments
from synapse_channel.core import hub_http, secure
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_exposure import exposure_problems
from synapse_channel.core.journal import EventKind
from synapse_channel.core.paranoid import ParanoidModeError, apply_paranoid_hub_profile
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.record_keeping import (
    OPERATION_RECORD,
    RISK_INDICATOR,
    purpose_for_kind,
)
from synapse_channel.core.token_audit import (
    METRICS_SET_APPLIED_KIND,
    METRICS_SET_REJECTED_KIND,
    metrics_token_audit_for,
)
from synapse_channel.core.token_set import (
    TokenSetAuthenticator,
    connect_token_digest,
    metrics_token_configured,
)
from synapse_channel.core.universal_receipts import NON_RECEIPT_EVENT_KINDS


def _entry(token_id: str, secret: str, **extra: Any) -> dict[str, Any]:
    """Return one token-set entry for ``secret``; the file keeps only its digest."""
    return {"id": token_id, "sha256": connect_token_digest(secret), **extra}


def _write(path: Path, *entries: dict[str, Any], raw: str | None = None) -> None:
    """Replace the owner-only token-set file atomically (a new inode, so the change is seen)."""
    staging = path.with_name(path.name + ".new")
    staging.write_text(raw or json.dumps({"version": 1, "tokens": list(entries)}), "utf-8")
    staging.chmod(0o600)
    os.replace(staging, path)


def _request(authorization: str | None = None) -> Request:
    headers = Headers()
    if authorization is not None:
        headers["Authorization"] = authorization
    return Request("/metrics", headers)


def _admits(
    token_set: TokenAuthenticator | None, secret: str | None, fixed: str | None = None
) -> bool:
    authorization = None if secret is None else f"Bearer {secret}"
    return hub_http.metrics_authorised(
        _request(authorization),
        metrics_token=fixed,
        query_token_ok=False,
        metrics_token_set=token_set,
    )


def test_the_set_admits_its_entries_and_rotates_without_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "metrics-tokens.json"
    _write(path, _entry("scraper-1", "first-secret"))
    token_set = TokenSetAuthenticator(path)
    assert _admits(token_set, "first-secret")
    assert not _admits(token_set, "wrong-secret")
    assert not _admits(token_set, None)

    _write(path, _entry("scraper-2", "second-secret-longer"))
    assert _admits(token_set, "second-secret-longer")
    assert not _admits(token_set, "first-secret")

    _write(path, raw="{not json")
    assert _admits(token_set, "second-secret-longer")  # the last accepted set stays
    assert token_set.stale_code == "invalid"

    assert _admits(token_set, "fixed", fixed="fixed")
    assert _admits(None, None)
    assert not _admits(None, "anything", fixed="fixed")


def test_windows_and_agent_bindings_admit_no_metrics_request(tmp_path: Path) -> None:
    path = tmp_path / "metrics-tokens.json"
    _write(
        path,
        _entry("later", "later-secret", not_before="2030-01-01T00:00:00Z"),
        _entry("done", "done-secret", not_after="2001-01-01T00:00:00Z"),
        _entry("bound", "bound-secret", agents=["SYNAPSE/agent"]),
        _entry("open", "open-secret"),
    )
    token_set = TokenSetAuthenticator(path, clock=lambda: 1_800_000_000.0)
    assert [_admits(token_set, s) for s in ("later-secret", "done-secret", "bound-secret")] == [
        False,
        False,
        False,
    ]
    assert _admits(token_set, "open-secret")


def test_the_exposure_guard_accepts_a_metrics_token_set(tmp_path: Path) -> None:
    path = tmp_path / "metrics-tokens.json"
    _write(path, _entry("scraper", "secret"))
    token_set = TokenSetAuthenticator(path)
    connect = TokenAuthenticator(["connect"])
    guarded = exposure_problems(
        "0.0.0.0",  # nosec B104 - an exposure check, nothing binds
        authenticator=connect,
        enable_metrics=True,
        metrics_token=None,
        metrics_token_set=token_set,
    )
    assert guarded == []
    (problem,) = exposure_problems(
        "0.0.0.0",  # nosec B104 - an exposure check, nothing binds
        authenticator=connect,
        enable_metrics=True,
        metrics_token=None,
    )
    assert "--metrics-token-set" in problem
    hub = SynapseHub(authenticator=connect, enable_metrics=True, metrics_token_set=token_set)
    assert hub.metrics_token_set is token_set
    assert hub._exposure_problems("0.0.0.0") == []  # nosec B104


def _kinds(store: EventStore) -> list[tuple[str, dict[str, Any]]]:
    kinds = (METRICS_SET_APPLIED_KIND, METRICS_SET_REJECTED_KIND)
    return [(event.kind, dict(event.payload)) for event in store.iter_events(kinds=kinds)]


def test_applied_and_rejected_sets_are_audit_rows(tmp_path: Path) -> None:
    path = tmp_path / "metrics-tokens.json"
    _write(path, _entry("scraper-1", "first-secret"))
    token_set = TokenSetAuthenticator(path)
    store = EventStore(tmp_path / "hub.db")
    try:
        assert metrics_token_audit_for(None, store) is None
        assert metrics_token_audit_for(token_set, None) is None
        assert metrics_token_audit_for(TokenAuthenticator(["fixed"]), store) is None
        assert metrics_token_audit_for(token_set, store) is not None
        _write(path, raw="[]")
        token_set.refresh()
        _write(path, _entry("scraper-2", "second-secret-longer"))
        token_set.refresh()
        rows = _kinds(store)
    finally:
        store.close()
    assert [kind for kind, _ in rows] == [
        METRICS_SET_APPLIED_KIND,
        METRICS_SET_REJECTED_KIND,
        METRICS_SET_APPLIED_KIND,
    ]
    assert rows[0][1] == {"path": str(path), "token_ids": ["scraper-1"]}
    assert "version and tokens" in rows[1][1]["reason"]
    assert rows[2][1]["token_ids"] == ["scraper-2"]
    assert "secret" not in json.dumps(rows).replace("second-secret", "")


def test_the_audit_kinds_are_registered_like_the_connect_token_kinds() -> None:
    assert EventKind.METRICS_TOKEN_SET_APPLIED == METRICS_SET_APPLIED_KIND
    assert EventKind.METRICS_TOKEN_SET_REJECTED == METRICS_SET_REJECTED_KIND
    assert purpose_for_kind(METRICS_SET_APPLIED_KIND) == OPERATION_RECORD
    assert purpose_for_kind(METRICS_SET_REJECTED_KIND) == RISK_INDICATOR
    assert {
        EventKind.METRICS_TOKEN_SET_APPLIED,
        EventKind.METRICS_TOKEN_SET_REJECTED,
    } <= NON_RECEIPT_EVENT_KINDS


async def test_a_live_hub_rotates_its_metrics_bearer_and_journals_it(tmp_path: Path) -> None:
    path = tmp_path / "metrics-tokens.json"
    _write(path, _entry("scraper-1", "first-secret"))
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(
        hub_id="syn-metrics-set",
        enable_metrics=True,
        metrics_token_set=TokenSetAuthenticator(path),
        journal=store,
    )
    try:
        async with running_hub(hub) as (_, uri):
            status, _, body = await http_get(uri, "/metrics", authorization="Bearer first-secret")
            assert status == 200 and "synapse_up" in body
            _write(path, _entry("scraper-2", "second-secret-longer"))
            status, _, _ = await http_get(uri, "/metrics", authorization="Bearer first-secret")
            assert status == 401
            status, _, _ = await http_get(
                uri, "/health", authorization="Bearer second-secret-longer"
            )
            assert status == 200
        rows = _kinds(store)
    finally:
        store.close()
    assert [row[1]["token_ids"] for row in rows] == [["scraper-1"], ["scraper-2"]]


def _parsed(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_hub_admission_arguments(parser)
    return parser.parse_args(list(argv))


def test_the_cli_builds_the_set_and_refuses_it_beside_a_fixed_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "metrics-tokens.json"
    _write(path, _entry("scraper", "secret"))
    args = _parsed("--metrics-token-set", str(path))
    assert args.metrics_token_set == str(path)
    args.metrics_token = None
    built = _metrics_token_set(args)
    assert isinstance(built, TokenSetAuthenticator) and built.path == path
    assert _metrics_token_set(argparse.Namespace(metrics_token_set=None)) is None

    args.metrics_token = "fixed"
    with pytest.raises(HubStartupRefused):
        _metrics_token_set(args)
    assert "cannot be combined with --metrics-token" in capsys.readouterr().err

    args.metrics_token = None
    args.metrics_token_set = str(tmp_path / "missing.json")
    with pytest.raises(HubStartupRefused):
        _metrics_token_set(args)
    assert "missing.json" in capsys.readouterr().err


def _paranoid_args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "paranoid": True,
        "token": "connect",
        "db": "hub.db",
        "metrics": True,
        "metrics_token": None,
        "metrics_token_set": None,
        "message_auth_key": ["k:secret:*"],
        "require_message_auth": True,
        "require_acl": True,
        "acl_policy": "acl.json",
        "tls_certfile": "cert.pem",
        "tls_keyfile": "key.pem",
        "insecure_unbound_identity": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_the_hardening_presets_accept_a_metrics_token_set() -> None:
    assert metrics_token_configured(argparse.Namespace(metrics_token_set="set.json"))
    assert metrics_token_configured(argparse.Namespace(metrics_token="fixed"))
    assert not metrics_token_configured(argparse.Namespace())
    with pytest.raises(ParanoidModeError, match="--metrics-token or --metrics-token-set"):
        apply_paranoid_hub_profile(_paranoid_args())
    assert apply_paranoid_hub_profile(_paranoid_args(metrics_token_set="set.json")) is not None
    missing = " ".join(secure._missing_material(_paranoid_args()))
    assert "--metrics-token-set" in missing
    assert "metrics surface" not in " ".join(
        secure._missing_material(_paranoid_args(metrics_token_set="set.json"))
    )
