# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — TLS pin file O_NOFOLLOW regressions (SCH-H-NEW-14)

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.tls import HubTLSConfigError, certificate_sha256_pin


def _self_signed_pem(tmp_path: Path) -> Path:
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pin-test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow() - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    path = tmp_path / "peer.pem"
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    path.chmod(0o644)
    return path


def test_certificate_pin_reads_regular_public_pem(tmp_path: Path) -> None:
    path = _self_signed_pem(tmp_path)
    pin = certificate_sha256_pin(path)
    assert pin.startswith("sha256:")
    assert len(pin) > len("sha256:")


def test_certificate_pin_refuses_symlink(tmp_path: Path) -> None:
    real = _self_signed_pem(tmp_path)
    link = tmp_path / "alias.pem"
    link.symlink_to(real)
    with pytest.raises(HubTLSConfigError, match="could not load peer certificate"):
        certificate_sha256_pin(link)
