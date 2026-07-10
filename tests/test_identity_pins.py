# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — trust-on-first-use identity pin store

"""The hub's durable name→key pin table: recording, persistence, and refusal."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from synapse_channel.core.identity_pin_governance import pin_reclaim_denial
from synapse_channel.core.identity_pins import IdentityPin, IdentityPinStore

NAME = "PROJ/pinned-agent"
VALID_KEY = base64.b64encode(bytes(range(32))).decode("ascii")
OTHER_KEY = base64.b64encode(bytes(range(1, 33))).decode("ascii")


def test_an_in_memory_store_pins_for_the_hub_lifetime_only(tmp_path: Path) -> None:
    store = IdentityPinStore()
    assert store.path is None
    assert store.pinned(NAME) is None
    store.pin(NAME, key_id="machine-abc", public_key=VALID_KEY, now=123.0)
    pin = store.pinned(NAME)
    assert pin is not None
    assert (pin.key_id, pin.public_key, pin.pinned_at) == ("machine-abc", VALID_KEY, 123.0)
    assert len(store) == 1
    assert store.reclaim(NAME, expected_key_id="machine-abc") == pin
    assert store.pinned(NAME) is None
    # Nothing was persisted anywhere.
    assert list(tmp_path.iterdir()) == []


def test_a_file_backed_store_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "pins.json"
    store = IdentityPinStore(path=path)
    store.pin(NAME, key_id="machine-abc", public_key=VALID_KEY, now=123.0)
    store.pin("PROJ/other", key_id="machine-def", public_key=OTHER_KEY, now=124.0)

    reloaded = IdentityPinStore(path=path)
    pin = reloaded.pinned(NAME)
    assert pin is not None
    assert pin.key_id == "machine-abc"
    assert pin.public_key == VALID_KEY
    assert pin.pinned_at == 123.0
    assert len(reloaded) == 2
    # The file is inspectable JSON with sorted names, so operators can inspect
    # the exact expected key before using the governed recovery path.
    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data["pins"]) == sorted(data["pins"])


def test_repinning_a_name_replaces_its_key(tmp_path: Path) -> None:
    path = tmp_path / "pins.json"
    store = IdentityPinStore(path=path)
    store.pin(NAME, key_id="machine-abc", public_key=VALID_KEY, now=1.0)
    store.pin(NAME, key_id="machine-def", public_key=OTHER_KEY, now=2.0)
    pin = IdentityPinStore(path=path).pinned(NAME)
    assert pin is not None
    assert pin.key_id == "machine-def"
    assert pin.public_key == OTHER_KEY


def test_a_malformed_key_can_never_be_pinned(tmp_path: Path) -> None:
    store = IdentityPinStore()
    with pytest.raises(ValueError):
        store.pin(NAME, key_id="k", public_key="not-base64!", now=1.0)
    with pytest.raises(ValueError):
        store.pin(NAME, key_id="k", public_key=base64.b64encode(b"short").decode(), now=1.0)
    assert store.pinned(NAME) is None


def test_a_malformed_pin_file_is_refused_loudly(tmp_path: Path) -> None:
    # Silently discarding pins would be a security downgrade, so every
    # malformation is an error: bad JSON, wrong shape, or a bad key inside.
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        IdentityPinStore(path=bad_json)

    wrong_shape = tmp_path / "shape.json"
    wrong_shape.write_text(json.dumps({"pins": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        IdentityPinStore(path=wrong_shape)

    bad_key = tmp_path / "key.json"
    bad_key.write_text(
        json.dumps({"pins": {NAME: {"key_id": "k", "public_key": "junk"}}}), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        IdentityPinStore(path=bad_key)

    bad_entry = tmp_path / "entry.json"
    bad_entry.write_text(json.dumps({"pins": {NAME: "junk"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object"):
        IdentityPinStore(path=bad_entry)


def test_a_missing_file_starts_empty_and_is_created_on_first_pin(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "pins.json"
    store = IdentityPinStore(path=path)
    assert len(store) == 0
    assert not path.exists()
    store.pin(NAME, key_id="machine-abc", public_key=VALID_KEY, now=1.0)
    assert path.is_file()
    # No temporary residue from the atomic write.
    assert [p.name for p in path.parent.iterdir()] == ["pins.json"]


def test_reclaim_is_compare_and_swap_and_persists_without_touching_other_names(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pins.json"
    store = IdentityPinStore(path=path)
    store.pin(NAME, key_id="machine-abc", public_key=VALID_KEY, now=1.0)
    store.pin("PROJ/other", key_id="machine-def", public_key=OTHER_KEY, now=2.0)

    assert store.reclaim(NAME, expected_key_id="stale-observation") is None
    assert IdentityPinStore(path=path).pinned(NAME) is not None

    removed = store.reclaim(NAME, expected_key_id="machine-abc")
    assert removed is not None and removed.key_id == "machine-abc"
    reloaded = IdentityPinStore(path=path)
    assert reloaded.pinned(NAME) is None
    assert reloaded.pinned("PROJ/other") is not None
    assert store.reclaim(NAME, expected_key_id="machine-abc") is None


def test_failed_reclaim_replace_keeps_the_live_pin_and_cleans_tempfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "pins.json"
    store = IdentityPinStore(path=path)
    store.pin(NAME, key_id="machine-abc", public_key=VALID_KEY, now=1.0)

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk unavailable"):
        store.reclaim(NAME, expected_key_id="machine-abc")

    assert store.pinned(NAME) is not None
    assert [entry.name for entry in tmp_path.iterdir()] == ["pins.json"]


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"pin_name": ""}, "pin name is required"),
        ({"expected_key_id": ""}, "expected key id is required"),
        ({"reason": ""}, "non-empty operator reason"),
        ({"reason": "x" * 501}, "exceeds 500"),
        ({"pin_name": "OPS/operator"}, "own live connection"),
        ({"acl_allowed": False}, "no identity-pin-reclaim ACL grant"),
        ({"requester_bound": False}, "not cryptographically bound"),
        ({"journal_available": False}, "no durable journal"),
        ({"pin": None}, "no identity pin"),
        (
            {"pin": IdentityPin("other", VALID_KEY, 1.0)},
            "does not match the expected key id",
        ),
        ({"owner_online": True}, "pinned identity is online"),
        ({"lease_live": True}, "ownership lease is still live"),
    ],
)
def test_pin_reclaim_policy_fails_closed_at_each_independent_gate(
    overrides: dict[str, object], detail: str
) -> None:
    inputs: dict[str, object] = {
        "requester": "OPS/operator",
        "pin_name": NAME,
        "expected_key_id": "machine-abc",
        "reason": "recover wedged holder",
        "pin": IdentityPin("machine-abc", VALID_KEY, 1.0),
        "acl_allowed": True,
        "requester_bound": True,
        "journal_available": True,
        "owner_online": False,
        "lease_live": False,
        "break_glass": False,
    }
    inputs.update(overrides)
    assert detail in pin_reclaim_denial(**inputs)  # type: ignore[arg-type]
    if overrides in ({"owner_online": True}, {"lease_live": True}):
        inputs["break_glass"] = True
        assert pin_reclaim_denial(**inputs) == ""  # type: ignore[arg-type]


def test_pin_reclaim_policy_allows_a_stale_exact_pin() -> None:
    assert (
        pin_reclaim_denial(
            requester="OPS/operator",
            pin_name=NAME,
            expected_key_id="machine-abc",
            reason="recover wedged holder",
            pin=IdentityPin("machine-abc", VALID_KEY, 1.0),
            acl_allowed=True,
            requester_bound=True,
            journal_available=True,
            owner_online=False,
            lease_live=False,
            break_glass=False,
        )
        == ""
    )
