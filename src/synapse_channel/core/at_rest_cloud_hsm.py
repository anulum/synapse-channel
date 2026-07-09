# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — optional cloud HSM / cloud KMS key-encryption-key backend
"""Wrap the at-rest data key with a key-encryption key held in a cloud HSM or KMS.

Cloud providers expose hardware-backed master keys through a network API (AWS KMS /
CloudHSM, Azure Key Vault, Google Cloud KMS, or a self-hosted HSM front-end). This
backend plugs into the same wrapped-key file format as the passphrase, PKCS#11, and
TPM 2.0 backends (``backend`` = :data:`CLOUD_HSM_BACKEND`) and implements the same
:class:`~synapse_channel.core.at_rest.KeyEncryptionKey` shape.

Providers are pluggable:

* **local-aes-kw** — AES-KW under an operator-held 32-byte master key file (offline
  tests, air-gapped drills, and CI). The master key stays on disk with owner-only
  permissions; it is the software stand-in for a cloud CMK.
* **aws-kms** — optional ``boto3`` client calling KMS ``Encrypt`` / ``Decrypt`` so the
  customer master key never leaves AWS. Importing this module never requires
  ``boto3``; :func:`require_boto3` raises only when an AWS operation is attempted.

Envelope model: the long-term secret (cloud CMK or local master key) wraps a random
data key; bulk AES-GCM still runs in process. Rotating the cloud key re-wraps the same
data key without rewriting encrypted surfaces.
"""

from __future__ import annotations

import base64
import secrets
from pathlib import Path
from typing import Any, Protocol

from synapse_channel.core.at_rest import (
    KEY_BYTES,
    AtRestCipher,
    _read_wrapped_key_document,
    _write_new_key_file,
    _write_wrapped_key_document,
    check_key_file,
    load_key_file,
    unwrap_data_key,
    wrap_data_key,
)

CLOUD_HSM_BACKEND = "cloud-hsm"
"""Wrapped-key ``backend`` tag for a cloud HSM / cloud KMS key-encryption key."""

PROVIDER_LOCAL_AES_KW = "local-aes-kw"
"""Provider that wraps with AES-KW under a local 32-byte master key file."""

PROVIDER_AWS_KMS = "aws-kms"
"""Provider that wraps via AWS KMS Encrypt/Decrypt against a CMK."""

_SUPPORTED_PROVIDERS = frozenset({PROVIDER_LOCAL_AES_KW, PROVIDER_AWS_KMS})


class CloudHsmProvider(Protocol):
    """Wraps and unwraps a data key under a cloud (or simulated-cloud) master key."""

    @property
    def provider_id(self) -> str:
        """Stable provider tag recorded in the wrapped-key file."""

    @property
    def key_id(self) -> str:
        """Provider-specific key identifier (alias, ARN, path, or CMK id)."""

    def wrap(self, data_key: bytes) -> bytes:
        """Return ``data_key`` wrapped under the cloud master key."""

    def unwrap(self, wrapped: bytes) -> bytes:
        """Return the data key recovered from ``wrapped``."""

    def params(self) -> dict[str, Any]:
        """Return serialisable params stored next to the wrapped data key."""


class LocalAesKwCloudHsmProvider:
    """Cloud-HSM stand-in: RFC 3394 AES-KW under a local owner-only master key file.

    Suitable for tests, offline drills, and operators who want the cloud-HSM file
    format without a network call. The master key file must pass
    :func:`~synapse_channel.core.at_rest.check_key_file`.
    """

    def __init__(self, master_key: bytes, *, key_id: str) -> None:
        if len(master_key) != KEY_BYTES:
            raise ValueError(
                f"cloud HSM master key must be {KEY_BYTES} bytes, got {len(master_key)}"
            )
        if not key_id:
            raise ValueError("cloud HSM key_id must not be empty")
        self._master = bytes(master_key)
        self._key_id = key_id

    @classmethod
    def from_key_file(cls, path: str | Path) -> LocalAesKwCloudHsmProvider:
        """Load a local master key file after ownership/mode checks."""
        target = Path(path)
        ok, reason = check_key_file(target)
        if not ok:
            raise ValueError(reason)
        return cls(load_key_file(target), key_id=str(target.resolve()))

    @property
    def provider_id(self) -> str:
        """Return the local AES-KW provider tag."""
        return PROVIDER_LOCAL_AES_KW

    @property
    def key_id(self) -> str:
        """Return the master-key path recorded as the key identifier."""
        return self._key_id

    def wrap(self, data_key: bytes) -> bytes:
        """Wrap ``data_key`` under the local master key with AES-KW."""
        return wrap_data_key(data_key, self._master)

    def unwrap(self, wrapped: bytes) -> bytes:
        """Unwrap the data key under the local master key with AES-KW."""
        return unwrap_data_key(wrapped, self._master)

    def params(self) -> dict[str, Any]:
        """Return serialisable params for the wrapped-key document."""
        return {"provider": PROVIDER_LOCAL_AES_KW, "key_id": self._key_id}


class AwsKmsCloudHsmProvider:
    """AWS KMS-backed cloud HSM provider (optional ``boto3``).

    The customer master key stays in AWS; only ciphertext leaves the service. Region
    comes from the constructor (or the usual AWS environment / config chain when
    omitted).
    """

    def __init__(
        self,
        key_id: str,
        *,
        region_name: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not key_id:
            raise ValueError("AWS KMS key_id must not be empty")
        self._key_id = key_id
        self._region_name = region_name
        self._client = client

    @property
    def provider_id(self) -> str:
        """Return the AWS KMS provider tag."""
        return PROVIDER_AWS_KMS

    @property
    def key_id(self) -> str:
        """Return the KMS key id / ARN / alias."""
        return self._key_id

    def _kms(self) -> Any:
        """Return a KMS client, building one lazily when none was injected."""
        if self._client is not None:
            return self._client
        boto3 = require_boto3()
        kwargs: dict[str, Any] = {}
        if self._region_name:
            kwargs["region_name"] = self._region_name
        self._client = boto3.client("kms", **kwargs)
        return self._client

    def wrap(self, data_key: bytes) -> bytes:
        """Wrap ``data_key`` with KMS Encrypt (AES-256 plaintext limit respected)."""
        if len(data_key) != KEY_BYTES:
            raise ValueError(f"data key must be {KEY_BYTES} bytes, got {len(data_key)}")
        response = self._kms().encrypt(KeyId=self._key_id, Plaintext=data_key)
        ciphertext = response.get("CiphertextBlob")
        if not isinstance(ciphertext, (bytes, bytearray)):
            raise ValueError("AWS KMS Encrypt returned no CiphertextBlob")
        return bytes(ciphertext)

    def unwrap(self, wrapped: bytes) -> bytes:
        """Recover the data key with KMS Decrypt."""
        try:
            response = self._kms().decrypt(CiphertextBlob=wrapped, KeyId=self._key_id)
        except Exception as exc:  # botocore ClientError, injected test failures, …
            raise ValueError(f"AWS KMS Decrypt failed: {exc}") from exc
        plaintext = response.get("Plaintext")
        if not isinstance(plaintext, (bytes, bytearray)):
            raise ValueError("AWS KMS Decrypt returned no Plaintext")
        recovered = bytes(plaintext)
        if len(recovered) != KEY_BYTES:
            raise ValueError(
                f"AWS KMS Decrypt returned {len(recovered)} bytes, expected {KEY_BYTES}"
            )
        return recovered

    def params(self) -> dict[str, Any]:
        """Return serialisable params for the wrapped-key document."""
        document: dict[str, Any] = {"provider": PROVIDER_AWS_KMS, "key_id": self._key_id}
        if self._region_name:
            document["region"] = self._region_name
        return document


class CloudHsmKeyEncryptionKey:
    """A key-encryption key backed by a :class:`CloudHsmProvider`."""

    def __init__(self, provider: CloudHsmProvider) -> None:
        self._provider = provider

    def wrap(self, data_key: bytes) -> bytes:
        """Wrap ``data_key`` under the cloud provider."""
        return self._provider.wrap(data_key)

    def unwrap(self, wrapped: bytes) -> bytes:
        """Unwrap the data key under the cloud provider."""
        return self._provider.unwrap(wrapped)


def require_boto3() -> Any:
    """Return the ``boto3`` module, raising a clear error when it is not installed.

    Raises
    ------
    RuntimeError
        When the optional ``boto3`` dependency is absent.
    """
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - exercised via a patched import in tests.
        raise RuntimeError(
            "the AWS KMS cloud-HSM backend requires the optional 'boto3' dependency; "
            "install it with: pip install synapse-channel[cloud-hsm]"
        ) from exc
    return boto3


def generate_wrapped_key_file_cloud_hsm(path: str | Path, *, provider: CloudHsmProvider) -> Path:
    """Write a random data key wrapped by a cloud HSM / KMS provider, owner-only.

    Parameters
    ----------
    path : str or pathlib.Path
        Destination wrapped-key file; refused when it already exists.
    provider : CloudHsmProvider
        Cloud (or simulated-cloud) master-key provider.

    Returns
    -------
    pathlib.Path
        The written wrapped-key file path.

    Raises
    ------
    FileExistsError
        When ``path`` already exists, so an existing key is never overwritten.
    ValueError
        When the provider rejects the wrap.
    """
    data_key = secrets.token_bytes(KEY_BYTES)
    kek = CloudHsmKeyEncryptionKey(provider)
    wrapped = kek.wrap(data_key)
    document = _write_wrapped_key_document(
        backend=CLOUD_HSM_BACKEND, params=provider.params(), wrapped=wrapped
    )
    return _write_new_key_file(Path(path), document)


def _provider_from_params(
    params: dict[str, Any],
    *,
    master_key_file: str | Path | None = None,
    aws_client: Any | None = None,
) -> CloudHsmProvider:
    """Rebuild a cloud HSM provider from a wrapped-key file's recorded params."""
    provider = params.get("provider")
    key_id = params.get("key_id")
    if not isinstance(provider, str) or not isinstance(key_id, str) or not key_id:
        raise ValueError("malformed cloud-HSM wrapped at-rest key file: missing provider/key_id")
    if provider not in _SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported cloud-HSM provider {provider!r}")
    if provider == PROVIDER_LOCAL_AES_KW:
        if master_key_file is None:
            # Prefer the path recorded at wrap time; allow override for relocated keys.
            master_key_file = key_id
        return LocalAesKwCloudHsmProvider.from_key_file(master_key_file)
    region = params.get("region")
    region_name = str(region) if isinstance(region, str) and region else None
    return AwsKmsCloudHsmProvider(key_id, region_name=region_name, client=aws_client)


def cipher_from_wrapped_key_file_cloud_hsm(
    path: str | Path,
    *,
    master_key_file: str | Path | None = None,
    aws_client: Any | None = None,
) -> AtRestCipher:
    """Build a cipher from a cloud-HSM-wrapped key file.

    Parameters
    ----------
    path : str or pathlib.Path
        Wrapped-key file written by :func:`generate_wrapped_key_file_cloud_hsm`.
    master_key_file : str or pathlib.Path, optional
        Override path for a local-aes-kw master key when the recorded path moved.
    aws_client : object, optional
        Injected AWS KMS client (tests); production builds one via ``boto3``.

    Returns
    -------
    AtRestCipher
        A cipher bound to the unwrapped data key.

    Raises
    ------
    ValueError
        When the file is not a cloud-HSM wrapped key file, its params are malformed,
        or unwrap fails.
    """
    backend, params, wrapped = _read_wrapped_key_document(Path(path))
    if backend != CLOUD_HSM_BACKEND:
        raise ValueError(f"wrapped key file uses the {backend!r} backend, not cloud-HSM: {path}")
    provider = _provider_from_params(params, master_key_file=master_key_file, aws_client=aws_client)
    data_key = CloudHsmKeyEncryptionKey(provider).unwrap(wrapped)
    return AtRestCipher(data_key)


def describe_cloud_hsm_document(path: str | Path) -> dict[str, Any]:
    """Return non-secret metadata from a cloud-HSM wrapped-key file for operators."""
    backend, params, wrapped = _read_wrapped_key_document(Path(path))
    if backend != CLOUD_HSM_BACKEND:
        raise ValueError(f"wrapped key file uses the {backend!r} backend, not cloud-HSM: {path}")
    return {
        "backend": backend,
        "provider": params.get("provider"),
        "key_id": params.get("key_id"),
        "region": params.get("region"),
        "wrapped_key_b64_len": len(base64.b64encode(wrapped)),
    }
