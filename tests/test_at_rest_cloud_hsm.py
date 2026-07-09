# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the cloud HSM / cloud KMS at-rest key backend

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.at_rest import (
    KEY_BYTES,
    WRAPPED_KEY_SCHEMA,
    generate_key_file,
    generate_wrapped_key_file,
)
from synapse_channel.core.at_rest_cloud_hsm import (
    CLOUD_HSM_BACKEND,
    PROVIDER_AWS_KMS,
    PROVIDER_LOCAL_AES_KW,
    AwsKmsCloudHsmProvider,
    LocalAesKwCloudHsmProvider,
    cipher_from_wrapped_key_file_cloud_hsm,
    describe_cloud_hsm_document,
    generate_wrapped_key_file_cloud_hsm,
    require_boto3,
)


def test_local_aes_kw_round_trip(tmp_path: Path) -> None:
    master = generate_key_file(tmp_path / "master.key")
    provider = LocalAesKwCloudHsmProvider.from_key_file(master)
    key_path = tmp_path / "cloud.wrapped.key"
    generate_wrapped_key_file_cloud_hsm(key_path, provider=provider)
    assert oct(key_path.stat().st_mode & 0o777) == "0o600"
    document = json.loads(key_path.read_text(encoding="utf-8"))
    assert document["schema"] == WRAPPED_KEY_SCHEMA
    assert document["backend"] == CLOUD_HSM_BACKEND
    assert document["params"]["provider"] == PROVIDER_LOCAL_AES_KW
    assert document["params"]["key_id"] == str(master.resolve())

    cipher = cipher_from_wrapped_key_file_cloud_hsm(key_path)
    blob = cipher.encrypt(b"cloud-hsm secret")
    reopened = cipher_from_wrapped_key_file_cloud_hsm(key_path, master_key_file=master)
    assert reopened.decrypt(blob) == b"cloud-hsm secret"


def test_local_aes_kw_wrong_master_fails(tmp_path: Path) -> None:
    master_a = generate_key_file(tmp_path / "a.key")
    master_b = generate_key_file(tmp_path / "b.key")
    key_path = tmp_path / "cloud.wrapped.key"
    generate_wrapped_key_file_cloud_hsm(
        key_path, provider=LocalAesKwCloudHsmProvider.from_key_file(master_a)
    )
    with pytest.raises(ValueError, match="cannot unwrap|wrong key-encryption"):
        cipher_from_wrapped_key_file_cloud_hsm(key_path, master_key_file=master_b)


def test_refuses_to_overwrite(tmp_path: Path) -> None:
    master = generate_key_file(tmp_path / "master.key")
    key_path = tmp_path / "cloud.wrapped.key"
    key_path.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        generate_wrapped_key_file_cloud_hsm(
            key_path, provider=LocalAesKwCloudHsmProvider.from_key_file(master)
        )


def test_rejects_passphrase_backend_file(tmp_path: Path) -> None:
    passphrase_path = tmp_path / "pass.wrapped.key"
    generate_wrapped_key_file(passphrase_path, "operator-passphrase")
    with pytest.raises(ValueError, match="not cloud-HSM"):
        cipher_from_wrapped_key_file_cloud_hsm(passphrase_path)


def test_master_key_must_be_owner_only_full_length(tmp_path: Path) -> None:
    bad = tmp_path / "bad.key"
    bad.write_bytes(b"\x00" * KEY_BYTES)
    bad.chmod(0o644)
    with pytest.raises(ValueError, match="owner-only"):
        LocalAesKwCloudHsmProvider.from_key_file(bad)


def test_empty_key_id_rejected() -> None:
    with pytest.raises(ValueError, match="key_id must not be empty"):
        LocalAesKwCloudHsmProvider(b"\x01" * KEY_BYTES, key_id="")


def test_aws_kms_provider_params_and_fail_closed_on_unknown_blob(tmp_path: Path) -> None:
    class _FakeKms:
        def __init__(self) -> None:
            self._store: dict[bytes, bytes] = {}

        def encrypt(self, *, KeyId: str, Plaintext: bytes) -> dict[str, Any]:
            assert KeyId == "alias/synapse-test"
            assert len(Plaintext) == KEY_BYTES
            blob = b"FAKEKMS" + Plaintext
            self._store[blob] = Plaintext
            return {"CiphertextBlob": blob}

        def decrypt(self, *, CiphertextBlob: bytes, KeyId: str) -> dict[str, Any]:
            assert KeyId == "alias/synapse-test"
            if CiphertextBlob not in self._store:
                raise KeyError("unknown ciphertext")
            return {"Plaintext": self._store[CiphertextBlob]}

    provider = AwsKmsCloudHsmProvider(
        "alias/synapse-test", region_name="eu-west-1", client=_FakeKms()
    )
    assert provider.provider_id == PROVIDER_AWS_KMS
    assert provider.params() == {
        "provider": PROVIDER_AWS_KMS,
        "key_id": "alias/synapse-test",
        "region": "eu-west-1",
    }
    key_path = tmp_path / "aws.wrapped.key"
    generate_wrapped_key_file_cloud_hsm(key_path, provider=provider)
    # A fresh client with an empty store cannot decrypt — fail closed.
    with pytest.raises(ValueError, match="AWS KMS Decrypt failed"):
        cipher_from_wrapped_key_file_cloud_hsm(key_path, aws_client=_FakeKms())


def test_aws_kms_round_trip_same_client(tmp_path: Path) -> None:
    class _FakeKms:
        def __init__(self) -> None:
            self._store: dict[bytes, bytes] = {}

        def encrypt(self, *, KeyId: str, Plaintext: bytes) -> dict[str, Any]:
            blob = b"FAKE" + Plaintext[::-1]
            self._store[blob] = Plaintext
            return {"CiphertextBlob": blob}

        def decrypt(self, *, CiphertextBlob: bytes, KeyId: str) -> dict[str, Any]:
            return {"Plaintext": self._store[CiphertextBlob]}

    client = _FakeKms()
    provider = AwsKmsCloudHsmProvider("arn:aws:kms:eu-west-1:123:key/abc", client=client)
    key_path = tmp_path / "aws.wrapped.key"
    generate_wrapped_key_file_cloud_hsm(key_path, provider=provider)
    cipher = cipher_from_wrapped_key_file_cloud_hsm(key_path, aws_client=client)
    assert cipher.decrypt(cipher.encrypt(b"kms-bound")) == b"kms-bound"


def test_describe_cloud_hsm_document(tmp_path: Path) -> None:
    master = generate_key_file(tmp_path / "master.key")
    key_path = tmp_path / "cloud.wrapped.key"
    generate_wrapped_key_file_cloud_hsm(
        key_path, provider=LocalAesKwCloudHsmProvider.from_key_file(master)
    )
    meta = describe_cloud_hsm_document(key_path)
    assert meta["backend"] == CLOUD_HSM_BACKEND
    assert meta["provider"] == PROVIDER_LOCAL_AES_KW
    assert meta["wrapped_key_b64_len"] > 0


def test_require_boto3_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _block(name: str, *args: object, **kwargs: object) -> object:
        if name == "boto3":
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _block)
    with pytest.raises(RuntimeError, match="boto3"):
        require_boto3()


def test_aws_kms_empty_key_id() -> None:
    with pytest.raises(ValueError, match="key_id must not be empty"):
        AwsKmsCloudHsmProvider("")
