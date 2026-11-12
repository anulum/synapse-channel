# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the suite's records: the case table, the key, the signature
"""Records are signed over their canonical bytes with an owner-only key and verify offline."""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from synapse_channel.core.aef_canonical import canonical_json
from synapse_channel.core.receipt_signing import (
    generate_receipt_signing_key,
    load_receipt_verification_key,
)
from synapse_channel.effect_suite.cases import VARIANTS, variant_named
from synapse_channel.effect_suite.record import (
    CUSTODY,
    RECORD_SCHEMA,
    SIGNATURE_DOMAIN,
    EffectSuiteKeyError,
    load_signing_key,
    record_bytes,
    sign_record,
    verification_refusal,
)

RECORD = {"schema": RECORD_SCHEMA, "variant": "1", "verdict": "pass", "case": 1}


def test_the_table_has_twenty_one_variants_and_one_is_not_applicable() -> None:
    names = [variant.name for variant in VARIANTS]
    assert len(names) == len(set(names)) == 21
    assert [variant.name for variant in VARIANTS if variant.expected is None] == ["3f"]
    assert variant_named("3d").title == "the base moved to a sibling before the publication"
    with pytest.raises(KeyError) as missing:
        variant_named("9z")
    assert missing.value.args == ("no effect-suite variant is called '9z'",)


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "suite.key"
    generate_receipt_signing_key(path)
    return path


def test_a_record_is_signed_over_its_canonical_bytes_and_verifies(key_file: Path) -> None:
    key = load_signing_key(key_file)
    public = load_receipt_verification_key(Path(str(key_file) + ".pub"))
    signed = sign_record(RECORD, key)
    assert record_bytes(signed) == canonical_json(RECORD)
    signature = signed["signature"]
    assert (signature["algorithm"], signature["key_id"], signature["custody"]) == (
        "ed25519",
        public.key_id,
        CUSTODY,
    )
    Ed25519PublicKey.from_public_bytes(public.public_key).verify(
        base64.b64decode(signature["value"]), SIGNATURE_DOMAIN + canonical_json(RECORD)
    )
    assert verification_refusal(signed, public) is None


def test_a_changed_unsigned_or_foreign_record_does_not_verify(
    key_file: Path, tmp_path: Path
) -> None:
    public = load_receipt_verification_key(Path(str(key_file) + ".pub"))
    signed = sign_record(RECORD, load_signing_key(key_file))
    other_path = tmp_path / "other.key"
    generate_receipt_signing_key(other_path)
    foreign = sign_record(RECORD, load_signing_key(other_path))
    garbled = {**signed, "signature": {**signed["signature"], "value": "%%%"}}
    assert verification_refusal({**signed, "verdict": "fail"}, public) == (
        "the signature does not match the record"
    )
    assert verification_refusal(garbled, public) == "the signature does not match the record"
    assert verification_refusal(RECORD, public) == "not a signed effect-suite record"
    assert verification_refusal({**signed, "schema": "other"}, public) == (
        "not a signed effect-suite record"
    )
    assert verification_refusal(foreign, public) == "signed by another key or algorithm"


def test_a_key_others_can_read_or_with_a_second_link_is_refused(key_file: Path) -> None:
    os.link(key_file, key_file.with_name("second-link.key"))
    with pytest.raises(EffectSuiteKeyError) as linked:
        load_signing_key(key_file)
    assert str(linked.value) == f"cannot use the signing key {key_file}: SecurePathError"
    os.unlink(key_file.with_name("second-link.key"))
    key_file.chmod(0o644)
    with pytest.raises(EffectSuiteKeyError) as readable:
        load_signing_key(key_file)
    assert str(readable.value) == f"cannot use the signing key {key_file}: SecurePathError"
    assert key_file.read_bytes().decode("ascii") not in str(readable.value)


def test_a_key_that_is_not_ed25519_or_not_a_key_is_refused(tmp_path: Path) -> None:
    other = tmp_path / "x25519.key"
    pem = X25519PrivateKey.generate().private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    other.write_bytes(pem)
    other.chmod(0o600)
    with pytest.raises(EffectSuiteKeyError) as wrong:
        load_signing_key(other)
    assert str(wrong.value) == f"the signing key {other} is not an Ed25519 key"
    junk = tmp_path / "junk.key"
    junk.write_bytes(b"not a key")
    junk.chmod(0o600)
    with pytest.raises(EffectSuiteKeyError) as unreadable:
        load_signing_key(junk)
    assert str(unreadable.value) == f"cannot use the signing key {junk}: ValueError"
