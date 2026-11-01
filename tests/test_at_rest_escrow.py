# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for threshold (Shamir) at-rest key escrow and recovery

from __future__ import annotations

import json
import secrets
from dataclasses import replace
from pathlib import Path

import pytest

from synapse_channel.core.at_rest import KEY_BYTES, generate_key_file, load_key_file
from synapse_channel.core.at_rest_escrow import (
    ESCROW_SHARE_SCHEMA,
    EscrowShare,
    load_escrow_share,
    recover_data_key,
    recover_key_file,
    split_data_key,
    split_key_file,
    write_escrow_share,
)


def test_split_and_recover_exact_threshold() -> None:
    secret = secrets.token_bytes(KEY_BYTES)
    shares = split_data_key(secret, threshold=3, share_count=5, group_id="g-test")
    assert len(shares) == 5
    assert all(s.group_id == "g-test" for s in shares)
    recovered = recover_data_key(shares[:3])
    assert recovered == secret
    # A different subset of three also works.
    assert recover_data_key((shares[0], shares[2], shares[4])) == secret


def test_all_shares_recover() -> None:
    secret = secrets.token_bytes(KEY_BYTES)
    shares = split_data_key(secret, threshold=2, share_count=4)
    assert recover_data_key(shares) == secret


def test_too_few_shares_fail() -> None:
    secret = secrets.token_bytes(KEY_BYTES)
    shares = split_data_key(secret, threshold=3, share_count=5)
    with pytest.raises(ValueError, match="need at least 3"):
        recover_data_key(shares[:2])


def test_wrong_share_group_fails() -> None:
    secret = secrets.token_bytes(KEY_BYTES)
    a = split_data_key(secret, threshold=2, share_count=2, group_id="a")
    b = split_data_key(secret, threshold=2, share_count=2, group_id="b")
    with pytest.raises(ValueError, match="same group_id"):
        recover_data_key((a[0], b[0]))


def test_duplicate_index_fails() -> None:
    secret = secrets.token_bytes(KEY_BYTES)
    shares = split_data_key(secret, threshold=2, share_count=3)
    with pytest.raises(ValueError, match="duplicate"):
        recover_data_key((shares[0], shares[0]))


def test_invalid_parameters() -> None:
    secret = secrets.token_bytes(KEY_BYTES)
    with pytest.raises(ValueError, match="at least 2"):
        split_data_key(secret, threshold=1, share_count=3)
    with pytest.raises(ValueError, match="share_count must be"):
        split_data_key(secret, threshold=3, share_count=2)
    with pytest.raises(ValueError, match="32 bytes"):
        split_data_key(b"short", threshold=2, share_count=2)


def test_key_file_round_trip(tmp_path: Path) -> None:
    key_path = generate_key_file(tmp_path / "store.key")
    original = load_key_file(key_path)
    out_dir = tmp_path / "shares"
    written = split_key_file(key_path, threshold=2, share_count=3, out_dir=out_dir)
    assert len(written) == 3
    from synapse_channel.core.secure_path import assert_owner_only_file_path

    for path in written:
        assert_owner_only_file_path(path, purpose="escrow share")
        share = load_escrow_share(path)
        assert share.to_document()["schema"] == ESCROW_SHARE_SCHEMA

    recovered_path = tmp_path / "recovered.key"
    recover_key_file([written[0], written[2]], out_path=recovered_path)
    assert load_key_file(recovered_path) == original
    assert_owner_only_file_path(recovered_path, purpose="recovered at-rest key")


def test_recover_refuses_overwrite(tmp_path: Path) -> None:
    key_path = generate_key_file(tmp_path / "store.key")
    written = split_key_file(key_path, threshold=2, share_count=2, out_dir=tmp_path / "s")
    existing = tmp_path / "out.key"
    existing.write_bytes(b"\x00" * KEY_BYTES)
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        recover_key_file(written, out_path=existing)


def test_malformed_share_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "nope"}), encoding="utf-8")
    with pytest.raises(ValueError, match="not a Synapse"):
        load_escrow_share(bad)


def test_write_share_document_round_trip(tmp_path: Path) -> None:
    secret = secrets.token_bytes(KEY_BYTES)
    share = split_data_key(secret, threshold=2, share_count=2)[0]
    path = write_escrow_share(tmp_path / "share.json", share)
    loaded = load_escrow_share(path)
    assert loaded == share


def test_split_key_file_rejects_bad_key(tmp_path: Path) -> None:
    bad = tmp_path / "bad.key"
    bad.write_bytes(b"\x00" * 8)
    with pytest.raises(ValueError):
        split_key_file(bad, threshold=2, share_count=2, out_dir=tmp_path / "out")


def test_split_refuses_more_than_255_shares() -> None:
    """GF(2^8) has 255 non-zero evaluation points, so a 256th share cannot exist."""
    with pytest.raises(ValueError, match="share_count must be <= 255"):
        split_data_key(secrets.token_bytes(KEY_BYTES), threshold=2, share_count=256)


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("threshold", "same threshold"),
        ("share_count", "same share_count"),
        ("payload", f"payload must be {KEY_BYTES} bytes"),
        ("index", "index out of range"),
    ],
)
def test_recover_refuses_shares_that_disagree_or_are_malformed(damage: str, message: str) -> None:
    """Each inconsistency between shares, and an index outside 1..255, is refused."""
    shares = list(split_data_key(secrets.token_bytes(KEY_BYTES), threshold=2, share_count=3))
    if damage == "threshold":
        shares[1] = replace(shares[1], threshold=3)
    elif damage == "share_count":
        shares[1] = replace(shares[1], share_count=4)
    elif damage == "payload":
        shares[1] = replace(shares[1], payload=b"short")
    else:
        shares[1] = replace(shares[1], index=256)
    with pytest.raises(ValueError, match=message):
        recover_data_key(shares)


def test_recover_refuses_an_empty_share_list() -> None:
    """Recovery without any share is refused before anything is read."""
    with pytest.raises(ValueError, match="at least one escrow share is required"):
        recover_data_key([])


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"index": "not-a-number"}, "malformed escrow share"),
        ({"payload": "!!not base64!!"}, "malformed escrow share"),
        ({"payload": "c2hvcnQ="}, "payload must be"),
        ({"threshold": 1}, "invalid threshold/index"),
        ({"share_count": 1}, "invalid threshold/index"),
        ({"index": 0}, "invalid threshold/index"),
    ],
)
def test_load_refuses_malformed_share_documents(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    """A share file with a bad field, payload or parameter is refused with its reason."""
    share = split_data_key(secrets.token_bytes(KEY_BYTES), threshold=2, share_count=3)[0]
    document = {**share.to_document(), **change}
    path = tmp_path / "share.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_escrow_share(path)


def test_recover_key_file_accepts_loaded_shares_and_paths_together(tmp_path: Path) -> None:
    """Shares already in memory and share files on disk recover the same key."""
    secret = secrets.token_bytes(KEY_BYTES)
    first, second, _third = split_data_key(secret, threshold=2, share_count=3)
    second_path = write_escrow_share(tmp_path / "second.json", second)
    assert isinstance(first, EscrowShare)
    out = recover_key_file([first, second_path], out_path=tmp_path / "recovered.key")
    assert load_key_file(out) == secret
