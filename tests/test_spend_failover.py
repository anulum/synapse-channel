# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — fencing a failed pool owner and handing the pool over (F02 phase 5)
"""An operator-signed revocation, an exact ledger copy and an attestation hand a pool over."""

from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.identity_keys import generate_signing_key, public_key_b64
from synapse_channel.core.spend_epoch import (
    SpendEpochError,
    sign_owner_revocation,
    usable_grant,
    verify_owner_revocation,
)
from synapse_channel.core.spend_ledger import NOT_ADMITTED, SpendLedger, SpendLedgerError
from test_spend_ledger import OWNER, PEER, T0, _config, _request, _settle

NEW_OWNER = "hub-new"
OPERATOR_KEY = generate_signing_key()
KEYS = [{"key_id": "operator-1", "public_key": public_key_b64(OPERATOR_KEY)}]


def _home(tmp_path: Path, name: str) -> Path:
    home = tmp_path / name
    home.mkdir(mode=0o700)
    return home


def _old_owner(tmp_path: Path, **config: Any) -> SpendLedger:
    ledger = SpendLedger(_home(tmp_path, "old") / "ledger.sqlite3", owner_hub_id=OWNER)
    ledger.configure(_config(revocation_keys=KEYS, **config), now=T0)
    return ledger


def _body(checkpoint: dict[str, object], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "pool_id": "pool-a",
        "revoked_epoch": checkpoint["epoch"],
        "new_epoch": int(str(checkpoint["epoch"])) + 1,
        "new_owner_hub_id": NEW_OWNER,
        "ledger_sequence": checkpoint["sequence"],
        "ledger_digest": checkpoint["digest"],
        "attestation": "host_retired",
        "cause": "old owner host lost",
        "issued_at": "2026-10-01T13:00:00Z",
    }
    body.update(overrides)
    return body


def _copy(old: SpendLedger, tmp_path: Path) -> SpendLedger:
    home = _home(tmp_path, "new")
    shutil.copy2(old.path, home / "ledger.sqlite3")
    return SpendLedger(home / "ledger.sqlite3", owner_hub_id=NEW_OWNER)


def test_a_new_owner_takes_over_and_the_old_epoch_is_fenced(tmp_path: Path) -> None:
    old = _old_owner(tmp_path)
    prior = old.reserve(PEER, _request(upper_bound="60"), now=T0)  # exposure 61 in epoch 1
    signed = sign_owner_revocation(_body(old.checkpoint("pool-a")), OPERATOR_KEY, "operator-1")
    new = _copy(old, tmp_path)

    assert new.fail_over("pool-a", signed, now=T0) == {
        "pool_id": "pool-a",
        "epoch": 2,
        "revision": 2,
    }
    status = new.status("pool-a", now=T0)
    assert (status["owner_hub_id"], status["epoch"], status["outstanding"]) == (NEW_OWNER, 2, "61")
    assert new.reserve(PEER, _request(key="k2", upper_bound="39"), now=T0)["reason"] == NOT_ADMITTED
    fresh = new.reserve(PEER, _request(key="k3", upper_bound="37"), now=T0)  # 61 + 38 <= 100
    assert fresh["admitted"] is True and fresh["epoch"] == 2
    settled = new.settle(PEER, _settle(str(prior["reservation_id"]), amount="50"), now=T0)
    assert settled["settled"] is True  # an old-epoch grant may still settle

    assert old.record_revocation("pool-a", signed, now=T0)["recorded"] is True
    assert old.record_revocation("pool-a", signed, now=T0)["recorded"] is False
    assert old.reserve(PEER, _request(key="k4", upper_bound="1"), now=T0)["reason"] == NOT_ADMITTED
    reasons = [e["body"]["reason"] for e in old.audit("pool-a") if e["kind"] == "refusal"]
    assert reasons == ["epoch_revoked"]

    revocation = verify_owner_revocation(signed, {"operator-1": KEYS[0]["public_key"]})
    assert usable_grant(prior, [revocation], now=T0) is False
    assert usable_grant(fresh, [revocation], now=T0) is True
    assert usable_grant({**prior, "pool_id": "pool-b"}, [revocation], now=T0) is True
    assert usable_grant({"admitted": False, "reason": NOT_ADMITTED}, [], now=T0) is False
    assert usable_grant({**fresh, "epoch": True}, [], now=T0) is False


def test_a_stale_copy_another_owner_or_a_wrong_epoch_is_refused(tmp_path: Path) -> None:
    old = _old_owner(tmp_path)
    signed = sign_owner_revocation(_body(old.checkpoint("pool-a")), OPERATOR_KEY, "operator-1")
    old.reserve(PEER, _request(), now=T0 + timedelta(seconds=1))  # the copy is now stale
    new = _copy(old, tmp_path)
    with pytest.raises(SpendLedgerError, match="does not match"):
        new.fail_over("pool-a", signed, now=T0)
    elsewhere = SpendLedger(new.path, owner_hub_id="hub-other")
    with pytest.raises(SpendLedgerError, match="another hub"):
        elsewhere.fail_over("pool-a", signed, now=T0)
    wrong_epoch = sign_owner_revocation(
        _body(old.checkpoint("pool-a"), revoked_epoch=5, new_epoch=6), OPERATOR_KEY, "operator-1"
    )
    with pytest.raises(SpendLedgerError, match="current epoch"):
        new.fail_over("pool-a", wrong_epoch, now=T0)
    other_pool = sign_owner_revocation(
        _body(old.checkpoint("pool-a"), pool_id="pool-b"), OPERATOR_KEY, "operator-1"
    )
    with pytest.raises(SpendLedgerError, match="another pool"):
        new.fail_over("pool-a", other_pool, now=T0)
    with pytest.raises(SpendLedgerError, match="no such pool"):
        new.fail_over("pool-z", signed, now=T0)
    with pytest.raises(SpendLedgerError, match="no such pool"):
        new.checkpoint("pool-z")
    naive = T0.replace(tzinfo=None)
    with pytest.raises(SpendLedgerError, match="UTC offset"):
        new.fail_over("pool-a", signed, now=naive)
    with pytest.raises(SpendLedgerError, match="UTC offset"):
        new.record_revocation("pool-a", signed, now=naive)


def test_only_a_configured_operator_key_can_revoke(tmp_path: Path) -> None:
    old = _old_owner(tmp_path)
    body = _body(old.checkpoint("pool-a"))
    stranger = sign_owner_revocation(body, generate_signing_key(), "operator-1")
    unknown = sign_owner_revocation(body, OPERATOR_KEY, "operator-9")
    tampered = sign_owner_revocation(body, OPERATOR_KEY, "operator-1")
    tampered["revocation"] = {**body, "cause": "edited after signing"}
    garbled = {**sign_owner_revocation(body, OPERATOR_KEY, "operator-1"), "signature": "%%"}
    for document, match in (
        (stranger, "does not verify"),
        (unknown, "does not trust"),
        (tampered, "does not verify"),
        (garbled, "does not verify"),
        ({"revocation": body}, "exactly revocation"),
        ({**unknown, "key_id": 7}, "does not trust"),
    ):
        with pytest.raises(SpendLedgerError, match=match):
            old.record_revocation("pool-a", document, now=T0)
    keyless = SpendLedger(_home(tmp_path, "keyless") / "l.sqlite3", owner_hub_id=OWNER)
    keyless.configure(_config(), now=T0)
    signed = sign_owner_revocation(_body(keyless.checkpoint("pool-a")), OPERATOR_KEY, "operator-1")
    with pytest.raises(SpendLedgerError, match="does not trust"):
        keyless.record_revocation("pool-a", signed, now=T0)


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"new_epoch": 3}, "follow the revoked epoch"),
        ({"revoked_epoch": 0}, "positive integer"),
        ({"ledger_sequence": True}, "positive integer"),
        ({"ledger_digest": "abc"}, "SHA-256"),
        ({"attestation": "trust me"}, "attestation must be"),
        ({"cause": " "}, "needs a cause"),
        ({"issued_at": "yesterday"}, "ISO-8601"),
        ({"new_owner_hub_id": "bad id"}, "plain token"),
        ({"extra": 1}, "exactly the documented fields"),
    ],
)
def test_a_malformed_revocation_body_is_refused(change: dict[str, Any], match: str) -> None:
    checkpoint = {"epoch": 1, "sequence": 3, "digest": "0" * 64}
    with pytest.raises(SpendEpochError, match=match):
        sign_owner_revocation(_body(checkpoint, **change), OPERATOR_KEY, "operator-1")


@pytest.mark.parametrize(
    ("keys", "match"),
    [
        ([{"key_id": "k", "public_key": "short"}], "base64 Ed25519"),
        ([{"key_id": "k"}], "exactly the fields"),
        ("not a list", "at most 8"),
        (KEYS * 9, "at most 8"),
    ],
)
def test_revocation_keys_in_the_configuration_are_strict(
    tmp_path: Path, keys: object, match: str
) -> None:
    ledger = SpendLedger(_home(tmp_path, "strict") / "l.sqlite3", owner_hub_id=OWNER)
    with pytest.raises(SpendLedgerError, match=match):
        ledger.configure(_config(revocation_keys=keys), now=T0)
