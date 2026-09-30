# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — `synapse spend` and `synapse hub --spend-ledger` (F02)
"""The operator commands act on a real ledger file; ``request`` asks a real owner hub."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from cli_processes_helpers import _hub_ns
from cli_processes_hub_helpers import _close_runner, _federation_store
from hub_e2e_helpers import running_hub
from synapse_channel import cli_processes
from synapse_channel.cli import build_parser
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.spend_ledger import SpendLedger
from test_multihub_identity_grant import FOLLOWER, IDENTITY_KEY
from test_spend_peer_e2e import OWNER, _hub, _ledger, _material, _pool, _reserve


def _file(path: Path, document: object) -> str:
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o600)
    return str(path)


def _run(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


def test_the_operator_configures_inspects_and_reconciles(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "spend"
    home.mkdir(mode=0o700)
    base = ["--ledger", str(home / "ledger.sqlite3"), "--hub-id", OWNER]
    assert _run(["spend", "configure", *base, "--file", _file(tmp_path / "p.json", _pool())]) == 0
    assert json.loads(capsys.readouterr().out) == {"pool_id": "pool-a", "revision": 1}
    ledger = SpendLedger(home / "ledger.sqlite3", owner_hub_id=OWNER)
    grant = ledger.reserve(FOLLOWER, _reserve(), now=datetime.now(timezone.utc))
    assert _run(["spend", "status", *base, "--pool", "pool-a"]) == 0
    assert json.loads(capsys.readouterr().out)["outstanding"] == "20"
    reconcile = {
        "pool_id": "pool-a",
        "reservation_id": grant["reservation_id"],
        "amount": "0",
        "evidence_ref": "cancelled-1",
        "cause": "call cancelled",
    }
    assert _run(["spend", "reconcile", *base, "--file", _file(tmp_path / "r.json", reconcile)]) == 0
    capsys.readouterr()
    assert _run(["spend", "audit", *base, "--pool", "pool-a"]) == 0
    kinds = [event["kind"] for event in json.loads(capsys.readouterr().out)]
    assert kinds == ["pool_config", "grant", "reconciliation"]
    assert _run(["spend", "status", *base, "--pool", "pool-z"]) == 2
    assert "no such pool" in capsys.readouterr().err
    (tmp_path / "bad.json").write_text("[1]", encoding="utf-8")
    (tmp_path / "bad.json").chmod(0o600)
    assert _run(["spend", "configure", *base, "--file", str(tmp_path / "bad.json")]) == 2
    assert "JSON object" in capsys.readouterr().err
    (tmp_path / "junk.json").write_text("{", encoding="utf-8")
    (tmp_path / "junk.json").chmod(0o600)
    assert _run(["spend", "configure", *base, "--file", str(tmp_path / "junk.json")]) == 2
    (tmp_path / "shared.json").write_text("{}", encoding="utf-8")
    (tmp_path / "shared.json").chmod(0o644)
    assert _run(["spend", "configure", *base, "--file", str(tmp_path / "shared.json")]) == 2


async def test_request_asks_a_real_owner_hub(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _granted, _spare, trust = _material(tmp_path)
    key_file = tmp_path / "granted.pem"
    token = _file(tmp_path / "token", "unused")
    ledger = _ledger(tmp_path)
    async with running_hub(_hub(trust, ledger)) as (_hub_ref, uri):
        base = ["spend", "request", "--uri", uri, "--local-id", FOLLOWER, "--timeout", "5"]
        signing = ["--peer-identity-key", str(key_file), "--peer-identity-key-id", IDENTITY_KEY]
        admitted = await asyncio.to_thread(
            _run, [*base, "reserve", "--file", _file(tmp_path / "a.json", _reserve()), *signing]
        )
        admitted_out = capsys.readouterr().out
        refused = await asyncio.to_thread(
            _run,
            [
                *base,
                "reserve",
                "--file",
                _file(tmp_path / "b.json", _reserve(key="k2", upper_bound="99")),
                *signing,
            ],
        )
        unsigned = await asyncio.to_thread(
            _run, [*base, "query", "--file", _file(tmp_path / "q.json", {}), "--token-file", token]
        )
        half = await asyncio.to_thread(
            _run, [*base, "query", "--file", str(tmp_path / "q.json"), "--peer-identity-key", "x"]
        )
    capsys.readouterr()
    assert admitted == 0 and json.loads(admitted_out)["admitted"] is True
    assert refused == 1
    assert unsigned == 2  # the identity-bound owner closes an unsigned registration
    assert half == 2


def _serving_policy_file(tmp_path: Path) -> str:
    store = Path(_federation_store(tmp_path))
    client_ca = tmp_path / "client-ca.pem"
    client_ca.write_text("public CA material\n", encoding="utf-8")
    client_ca.chmod(0o600)
    policy: dict[str, Any] = {
        "version": 1,
        "federation_store": store.name,
        "client_ca_file": client_ca.name,
        "grants": [
            {
                "sender": "fleet-a",
                "domain_id": "domain-b",
                "namespace": "SYNAPSE-CHANNEL",
                "signing_key_id": "domain-b:main",
            }
        ],
    }
    return _file(tmp_path / "serving-policy.json", policy)


def test_the_hub_flag_needs_its_partners_and_a_valid_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "spend"
    home.mkdir(mode=0o700)
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    def build_tls(**_kwargs: Any) -> None:
        return None

    policy = _serving_policy_file(tmp_path)
    tls = {"tls_certfile": "c.pem", "tls_keyfile": "k.pem"}
    assert (
        cli_processes._cmd_hub(
            _hub_ns(spend_ledger=str(home / "l.sqlite3"), hub_id="hub-x"),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 2
    )
    assert "--multihub-serving-policy" in capsys.readouterr().err
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                spend_ledger=str(shared / "l.sqlite3"),
                hub_id="hub-x",
                multihub_serving_policy=policy,
                **tls,
            ),
            runner=_close_runner,
            hub_factory=build_hub,
            tls_context_factory=build_tls,
        )
        == 2
    )
    assert "spend ledger directory" in capsys.readouterr().err
    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                spend_ledger=str(home / "l.sqlite3"),
                hub_id="hub-x",
                multihub_serving_policy=policy,
                **tls,
            ),
            runner=_close_runner,
            hub_factory=build_hub,
            tls_context_factory=build_tls,
        )
        == 0
    )
    ledger = captured["spend_ledger"]
    assert isinstance(ledger, SpendLedger) and ledger.owner_hub_id == "hub-x"
