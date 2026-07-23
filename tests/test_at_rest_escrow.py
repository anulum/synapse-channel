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
from pathlib import Path

import pytest

from synapse_channel.core.at_rest import KEY_BYTES, generate_key_file, load_key_file
from synapse_channel.core.at_rest_escrow import (
    ESCROW_SHARE_SCHEMA,
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
