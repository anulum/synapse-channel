# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a pool advertisement reaches the journal and no seat (F03 option A)
"""An owner's redacted pool advertisement on a real hub, and the CLI that sends it."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import Recorder, running_hub
from synapse_channel.cli import build_parser
from synapse_channel.cli_entitlements import _advertise, _send_advert
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.acl import ENTITLEMENT_ADVERTISE, AclPolicy, AclRule
from synapse_channel.core.entitlement_advert import build_advert
from synapse_channel.core.entitlement_store import append_event
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.identity_binding import load_identity_trust_bundle
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from test_entitlement_advert import ledger
from test_hub_identity_enrollment import Machines, _connected, _trust

OWNER = "OPS/owner"
WATCHER = "OPS/watcher"
AT = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)


def _hub(tmp_path: Path, machines: Machines, *, journal: bool = True, bound: bool = True) -> Any:
    trust = _trust(tmp_path, machines, ("owner", OWNER), ("watcher", WATCHER))
    return SynapseHub(
        journal=EventStore(tmp_path / "events.db") if journal else None,
        identity_trust_bundle=load_identity_trust_bundle(trust),
        require_identity_binding=bound,
        identity_pin_path=tmp_path / "pins.json",
        acl_policy=AclPolicy([AclRule(ENTITLEMENT_ADVERTISE, "pool-alias", "gpu-*", "OPS", "x")]),
    )


def _advert(alias: str = "gpu-a") -> dict[str, Any]:
    return build_advert(ledger(), pool_id="pool-1", alias=alias, as_of=AT)


async def _send(agent: SynapseAgent, inbox: Recorder, advert: object) -> dict[str, Any]:
    inbox.messages.clear()
    await agent.send_message(MessageType.ENTITLEMENT_ADVERT, target="System", advert=advert)
    return await inbox.wait_for(lambda m: m.get("type") == MessageType.ENTITLEMENT_ADVERT_RESULT)


def _rows(tmp_path: Path) -> list[dict[str, Any]]:
    store = EventStore(tmp_path / "events.db")
    try:
        return [dict(e.payload) for e in store.read_all() if e.kind == EventKind.ENTITLEMENT_ADVERT]
    finally:
        store.close()


async def test_an_advertisement_is_journalled_and_reaches_no_seat(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines)
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(WATCHER, uri, machines.kwargs("watcher")) as (_watcher, seen):
            async with _connected(OWNER, uri, machines.kwargs("owner")) as (owner, inbox):
                applied = await _send(owner, inbox, _advert())
                refusals = [
                    await _send(owner, inbox, _advert("cpu-x")),
                    await _send(owner, inbox, {**_advert(), "pool_id": "pool-1"}),
                    await _send(owner, inbox, "not an advert"),
                ]
            await asyncio.sleep(0.2)
            watcher_text = json.dumps(seen.messages)
    hub.journal.close()
    assert applied["applied"] is True and applied["audit_seq"]
    details = [refusal["payload"] for refusal in refusals]
    assert "not authorised to advertise" in details[0]
    assert "exactly the documented fields" in details[1]
    assert "not authorised to advertise" in details[2]  # no alias to authorise
    assert "gpu-a" not in watcher_text and MessageType.ENTITLEMENT_ADVERT not in watcher_text
    [row] = _rows(tmp_path)
    assert row["advertiser"] == OWNER and row["hub_id"] == hub.hub_id
    assert row["advert"] == _advert()


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [({"journal": False}, "durable journal"), ({"bound": False}, "cryptographically proven")],
)
async def test_a_hub_without_a_journal_or_proof_refuses(
    tmp_path: Path, overrides: dict[str, bool], detail: str
) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines, **overrides)
    key = {} if not overrides.get("bound", True) else machines.kwargs("owner")
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(OWNER, uri, key) as (owner, inbox):
            result = await _send(owner, inbox, _advert())
    if hub.journal is not None:
        hub.journal.close()
    assert result["applied"] is False and detail in result["payload"]


async def test_a_pinned_owner_is_proven_and_its_pin_key_is_recorded(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    # no binding requirement: the owner is proven by the key the hub pinned at first use
    hub = _hub(tmp_path, machines, bound=False)
    owner_key_id, _public = machines.public("owner")
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(OWNER, uri, machines.kwargs("owner")) as (owner, inbox):
            result = await _send(owner, inbox, _advert())
    hub.journal.close()
    assert result["applied"] is True, result
    [row] = _rows(tmp_path)
    assert row["advertiser_key_id"] == owner_key_id


def _factory(key: dict[str, Any]) -> Any:
    def build(name: str, callback: Any, **kwargs: Any) -> SynapseAgent:
        return SynapseAgent(name, callback, machine_identity=False, **key, **kwargs)

    return build


async def test_the_cli_sends_the_advertisement(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines)
    common: dict[str, Any] = {
        "name": OWNER,
        "token": None,
        "ready_timeout": 3.0,
        "result_timeout": 3.0,
        "agent_factory": _factory(machines.kwargs("owner")),
    }
    async with running_hub(hub) as (_hub_ref, uri):
        assert await _send_advert(_advert(), uri=uri, **common) == 0
        applied = capsys.readouterr().out
        assert await _send_advert(_advert("cpu-x"), uri=uri, **common) == 1
        refused = capsys.readouterr().out
        silent = await _send_advert(_advert(), uri=uri, **{**common, "result_timeout": 0.0})
        silent_text = capsys.readouterr().out
        unproven = await _send_advert(
            _advert(), uri=uri, **{**common, "agent_factory": _factory({})}
        )
        unproven_text = capsys.readouterr().out
    hub.journal.close()
    assert "advertisement recorded for 'gpu-a'" in applied and "(audit seq " in applied
    assert refused.startswith("advertisement refused:")
    assert silent == 2 and "no verdict" in silent_text
    assert unproven == 2 and OWNER in unproven_text


def test_the_cli_dry_run_prints_the_redacted_advertisement(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "ledger.sqlite3"
    for event in ledger():
        append_event(store, event)
    parser = build_parser()
    args = parser.parse_args(
        [
            "entitlements",
            "advertise",
            "--pool",
            "pool-1",
            "--alias",
            "gpu-a",
            "--store",
            str(store),
            "--dry-run",
        ]
    )
    assert args.func is _advertise
    assert _advertise(args) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["pool_alias"] == "gpu-a" and "pool-1" not in json.dumps(printed)
    missing = parser.parse_args(
        ["entitlements", "advertise", "--pool", "pool-9", "--alias", "gpu-a", "--store", str(store)]
    )
    assert _advertise(missing) == 2
    assert "no such pool" in capsys.readouterr().err
    unreachable = parser.parse_args(
        [
            "entitlements",
            "advertise",
            "--pool",
            "pool-1",
            "--alias",
            "gpu-a",
            "--store",
            str(store),
            "--uri",
            "ws://127.0.0.1:9",
            "--name",
            OWNER,
            "--ready-timeout",
            "0.2",
        ]
    )
    assert _advertise(unreachable) == 2
