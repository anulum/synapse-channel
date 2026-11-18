# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — GET /setup/receipts.json: the setup receipt chain as an admin may read it
"""The receipts route reads the chain as written: newest first, or one receipt by sequence."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import setup_plan_fixture as fixture
import synapse_channel.dashboard_setup_http as setup_http
from synapse_channel.dashboard_access_http import AccessHttpDecision
from synapse_channel.dashboard_setup_contract import SetupPostureDecision
from synapse_channel.dashboard_setup_http import (
    MAX_SETUP_RECEIPTS_LISTED,
    SETUP_RECEIPTS_PATH,
    SetupServing,
    setup_read_response,
    setup_receipts_response,
)
from synapse_channel.dashboard_setup_planner import SetupPlanStore
from synapse_channel.dashboard_setup_receipts import SetupReceiptStore
from test_dashboard_setup_http import (
    TOKENS,
    _access_file,
    _fake_inspect,
    _get,
    _receipts,
    _start,
)
from test_dashboard_setup_plan_http import ADMIN, OPERATOR, _policy, _post
from test_dashboard_setup_receipts import _draft


def _serving(receipts: Path) -> SetupServing:
    return SetupServing(
        SetupPostureDecision(True, "ready"), receipts, fixture.profile(), SetupPlanStore()
    )


def _chain(receipts: Path, count: int) -> None:
    with SetupReceiptStore(receipts) as chain:
        for index in range(count):
            chain.append(replace(_draft(), timestamp_ms=1_784_758_400_000 + index))


def _read(receipts: Path, query: str = "", token: str | None = ADMIN) -> AccessHttpDecision:
    authorization = None if token is None else f"Bearer {token}"
    return setup_receipts_response(authorization, query, _policy(), _serving(receipts))


def test_the_receipts_are_listed_newest_first_with_the_chains_count(tmp_path: Path) -> None:
    receipts = _receipts(tmp_path)
    _chain(receipts, 3)

    answer = _read(receipts)

    assert answer.status == 200
    document = json.loads(answer.body)
    assert document["count"] == 3
    assert [item["sequence"] for item in document["receipts"]] == [3, 2, 1]
    assert document["receipts"][0]["receipt_digest"]


def test_a_long_chain_lists_only_its_newest_receipts(tmp_path: Path) -> None:
    receipts = _receipts(tmp_path)
    _chain(receipts, MAX_SETUP_RECEIPTS_LISTED + 2)

    document = json.loads(_read(receipts).body)

    assert document["count"] == MAX_SETUP_RECEIPTS_LISTED + 2
    assert len(document["receipts"]) == MAX_SETUP_RECEIPTS_LISTED
    assert document["receipts"][-1]["sequence"] == 3


def test_one_receipt_is_read_by_its_sequence(tmp_path: Path) -> None:
    receipts = _receipts(tmp_path)
    _chain(receipts, 2)

    answer = _read(receipts, "sequence=2")
    absent = _read(receipts, "sequence=3")

    assert json.loads(answer.body)["receipt"]["sequence"] == 2
    assert (absent.status, absent.body) == (404, b"no setup receipt 3")


@pytest.mark.parametrize(
    "query",
    [
        "sequence=",
        "sequence=0",
        "sequence=-1",
        "sequence=x",
        "sequence=1&sequence=2",
        "sequence=%C2%B2",
        "sequence=9007199254740993",
    ],
)
def test_a_malformed_sequence_is_refused(tmp_path: Path, query: str) -> None:
    answer = _read(_receipts(tmp_path), query)

    assert (answer.status, answer.body) == (400, b"sequence must be one positive integer")


def test_an_unknown_caller_or_one_without_setup_plan_is_refused(tmp_path: Path) -> None:
    receipts = _receipts(tmp_path)

    assert _read(receipts, token=None).status == 401
    assert _read(receipts, token=OPERATOR).status == 403
    unserved = setup_receipts_response(
        f"Bearer {ADMIN}", "", _policy(), SetupServing(SetupPostureDecision(False, "unarmed"))
    )
    assert (unserved.status, unserved.body) == (404, b"setup not served: unarmed")


def test_an_unreadable_chain_is_unavailable(tmp_path: Path) -> None:
    receipts = _receipts(tmp_path)
    (receipts / "setup-receipts.db").mkdir()

    answer = _read(receipts)

    assert (answer.status, answer.body) == (503, b"setup receipt chain unavailable")


def test_the_setup_reads_route_each_path_to_its_answer(tmp_path: Path) -> None:
    receipts = _receipts(tmp_path)
    _chain(receipts, 1)

    listed = setup_read_response(
        SETUP_RECEIPTS_PATH,
        "",
        f"Bearer {ADMIN}",
        _policy(),
        _serving(receipts),
        fixture.inspection_of_profile,
    )
    preflight = setup_read_response(
        "/setup/preflight.json",
        "",
        f"Bearer {ADMIN}",
        _policy(),
        _serving(receipts),
        fixture.inspection_of_profile,
    )

    assert json.loads(listed.body)["count"] == 1
    assert json.loads(preflight.body)["profile"] == "local-single-user"


def test_a_real_start_reads_back_the_receipt_of_a_plan_it_issued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_http, "inspect_setup", _fake_inspect)
    server = _start(
        dashboard_access_file=_access_file(tmp_path),
        operator=True,
        setup_apply=True,
        setup_receipts=_receipts(tmp_path),
    )
    try:
        origin = server.url("").rstrip("/")
        planned = _post(server, TOKENS["admin"], origin=origin)
        read_back = _get(server, f"{SETUP_RECEIPTS_PATH}?sequence=1", TOKENS["admin"])
        refused = _get(server, SETUP_RECEIPTS_PATH, TOKENS["operator"])
    finally:
        server.close()

    assert (planned[0], read_back[0]) == (200, 200)
    receipt = json.loads(read_back[1])["receipt"]
    assert receipt["plan_id"] == json.loads(planned[1])["plan_id"]
    assert receipt["outcome"] == "planned"
    assert refused == (403, "setup_plan capability required")
