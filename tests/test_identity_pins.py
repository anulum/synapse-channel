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
from pathlib import Path

import pytest

from synapse_channel.core.identity_pins import IdentityPinStore

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
    # The file is inspectable JSON with sorted names, so operators can read
    # and hand-edit it (the documented recovery path).
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


def test_a_missing_file_starts_empty_and_is_created_on_first_pin(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "pins.json"
    store = IdentityPinStore(path=path)
    assert len(store) == 0
    assert not path.exists()
    store.pin(NAME, key_id="machine-abc", public_key=VALID_KEY, now=1.0)
    assert path.is_file()
    # No temporary residue from the atomic write.
    assert [p.name for p in path.parent.iterdir()] == ["pins.json"]
