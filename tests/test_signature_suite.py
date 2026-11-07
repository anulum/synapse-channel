# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Ed25519 and hybrid Ed25519 + ML-DSA-65 signature suites
"""Real keys only: the published vectors, the composite's separability, and the key files."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest
from tools import signature_suite_vectors

from _platform_caps import requires_posix_mode_bits
from synapse_channel.core import signature_suite
from synapse_channel.core.signature_suite import (
    COMPOSITE_LABEL,
    SUITE_ED25519,
    SUITE_HYBRID,
    SignatureSuiteError,
    SuiteKey,
    TrustedKey,
    b64,
    generate_key,
    key_document,
    load_key,
    load_trust,
    preimage,
    trust_document,
    unb64,
    verify,
)

FIXTURE = Path(__file__).parent / "fixtures" / "signature_suite_v0_1.json"


def _owner_only(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_every_published_vector_gets_its_recorded_verdict(
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    assert signature_suite_vectors.check(text) == []
    document = json.loads(text)
    expected = {vector["name"]: vector["expected"] for vector in document["suite_vectors"]}
    assert expected["hybrid-plain-preimage-halves"] == "invalid"
    assert expected["nul-in-domain"] == "refused"
    assert signature_suite_vectors.main(["--check"]) == 0
    assert "verifies" in capsys.readouterr().out


def test_a_vector_with_the_wrong_verdict_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document["suite_vectors"][0]["expected"] = "invalid"
    document["countersign_vectors"][0]["expected"] = "MISMATCH"
    document["hybrid_key_id"] = "0" * 16
    problems = signature_suite_vectors.check(json.dumps(document))
    assert len(problems) == 3
    assert problems[0] == "the hybrid key id does not come from the published seeds"


def test_the_vector_tool_writes_a_fresh_fixture_that_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = tmp_path / "vectors.json"
    monkeypatch.setattr(signature_suite_vectors, "FIXTURE", fixture)
    assert signature_suite_vectors.main(["--write"]) == 0
    assert signature_suite_vectors.check(fixture.read_text(encoding="utf-8")) == []
    document = json.loads(fixture.read_text(encoding="utf-8"))
    document["suite_vectors"][1]["signature_hex"] = "00" * 3373
    fixture.write_text(json.dumps(document), encoding="utf-8")
    assert signature_suite_vectors.main(["--check"]) == 1
    assert "hybrid-valid: invalid, expected valid" in capsys.readouterr().out


@pytest.mark.parametrize("suite", [SUITE_ED25519, SUITE_HYBRID])
def test_a_fresh_key_signs_and_verifies_and_nothing_else_does(suite: str) -> None:
    key = generate_key(suite)
    signature = key.sign("SYNAPSE-CHANNEL:TEST:v1", b"message")
    public = key.public_bytes()
    assert verify(suite, public, "SYNAPSE-CHANNEL:TEST:v1", b"message", signature)
    assert not verify(suite, public, "SYNAPSE-CHANNEL:TEST:v1", b"other", signature)
    assert not verify(suite, public, "SYNAPSE-CHANNEL:TEST:v2", b"message", signature)
    assert not verify(suite, public[:-1], "SYNAPSE-CHANNEL:TEST:v1", b"message", signature)
    assert not verify(suite, public, "SYNAPSE-CHANNEL:TEST:v1", b"message", signature + b"\x00")
    assert len(key.key_id()) == 16


def test_each_composite_half_commits_to_the_composite() -> None:
    hybrid = signature_suite_vectors.hybrid_key()
    signature = hybrid.sign("D", b"m")
    composite = preimage(SUITE_HYBRID, "D", b"m")
    assert composite == COMPOSITE_LABEL + b"\x00" + SUITE_HYBRID.encode() + b"\x00D\x00m"
    # The Ed25519 half is not a valid plain Ed25519 signature of the same message.
    ed_public = hybrid.public_bytes()[:32]
    assert not verify(SUITE_ED25519, ed_public, "D", b"m", signature[:64])
    # Each half alone fails; flipping one byte in either half fails.
    for index in (0, 63, 64, len(signature) - 1):
        flipped = bytearray(signature)
        flipped[index] ^= 1
        assert not verify(SUITE_HYBRID, hybrid.public_bytes(), "D", b"m", bytes(flipped))


@pytest.mark.parametrize(
    ("suite", "domain"),
    [("ed25519+ml-dsa-87/v1", "D"), (SUITE_HYBRID, "D\x00E"), (SUITE_ED25519, "\x00")],
)
def test_an_unknown_suite_or_a_nul_in_the_domain_is_refused(suite: str, domain: str) -> None:
    with pytest.raises(SignatureSuiteError):
        verify(suite, b"", domain, b"m", b"")
    with pytest.raises(SignatureSuiteError):
        preimage(suite, domain, b"m")


@pytest.mark.parametrize(
    ("suite", "ed", "ml", "message"),
    [
        (SUITE_ED25519, b"\x00" * 31, b"", "Ed25519 seed is 32 bytes"),
        (SUITE_ED25519, b"\x00" * 32, b"\x00" * 32, "holds 0 ML-DSA seed bytes"),
        (SUITE_HYBRID, b"\x00" * 32, b"", "holds 32 ML-DSA seed bytes"),
        ("rsa", b"\x00" * 32, b"", "unknown signature suite"),
    ],
)
def test_a_key_whose_seeds_do_not_fit_its_suite_is_refused(
    suite: str, ed: bytes, ml: bytes, message: str
) -> None:
    with pytest.raises(SignatureSuiteError, match=message):
        SuiteKey(suite, ed, ml)


def test_without_ml_dsa_the_composite_is_refused_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    hybrid = signature_suite_vectors.hybrid_key()
    signature = hybrid.sign("D", b"m")
    monkeypatch.setattr(signature_suite, "ML_DSA_AVAILABLE", False)
    for attempt in (
        lambda: generate_key(SUITE_HYBRID),
        lambda: hybrid.sign("D", b"m"),
        lambda: verify(SUITE_HYBRID, b"\x00" * 1984, "D", b"m", signature),
    ):
        with pytest.raises(SignatureSuiteError, match="needs ML-DSA-65"):
            attempt()
    assert generate_key(SUITE_ED25519).suite == SUITE_ED25519


def test_the_import_probe_reports_a_backend_without_ml_dsa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cryptography.hazmat.primitives import asymmetric

    assert signature_suite._probe_ml_dsa() is True
    monkeypatch.delattr(asymmetric, "mldsa")
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives.asymmetric.mldsa", None)
    assert signature_suite._probe_ml_dsa() is False


@requires_posix_mode_bits
def test_a_key_file_loads_only_owner_only_and_only_for_its_suite(tmp_path: Path) -> None:
    key = generate_key(SUITE_HYBRID)
    path = _owner_only(tmp_path / "pq.key", key_document(key))
    assert load_key(path, suite=SUITE_HYBRID) == key
    with pytest.raises(SignatureSuiteError, match="belongs to"):
        load_key(path, suite=SUITE_ED25519)
    ed = generate_key(SUITE_ED25519)
    ed_path = _owner_only(tmp_path / "ed.key", key_document(ed))
    assert load_key(ed_path, suite=SUITE_ED25519) == ed
    path.chmod(0o640)
    with pytest.raises(Exception, match="group|permission|owner"):
        load_key(path, suite=SUITE_HYBRID)


@requires_posix_mode_bits
@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "not JSON"),
        ('{"format": "other"}', "is not synapse-signature-key-v1"),
        (
            '{"format": "synapse-signature-key-v1", "suite": "ed25519", "ed25519_seed": 1}',
            "base64 text",
        ),
        (
            '{"format": "synapse-signature-key-v1", "suite": "ed25519", "ed25519_seed": "@@"}',
            "not valid base64",
        ),
        (
            '{"format": "synapse-signature-key-v1", "suite": "ed25519", "ed25519_seed": "AA"}',
            "not valid base64",
        ),
    ],
)
def test_a_malformed_key_file_is_refused(tmp_path: Path, text: str, message: str) -> None:
    path = _owner_only(tmp_path / "bad.key", text)
    with pytest.raises(SignatureSuiteError, match=message):
        load_key(path, suite=SUITE_ED25519)


def test_base64_must_be_canonical() -> None:
    assert unb64(b64(b"\x01\x02\x03"), "x") == b"\x01\x02\x03"
    with pytest.raises(SignatureSuiteError, match="not canonical"):
        unb64("AQJ=", "x")


def test_a_trust_file_names_derived_key_ids_only(tmp_path: Path) -> None:
    key = generate_key(SUITE_HYBRID)
    path = tmp_path / "trust.json"
    path.write_text(trust_document(key), encoding="utf-8")
    assert load_trust(path) == {key.key_id(): TrustedKey(SUITE_HYBRID, key.public_bytes())}


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (None, "cannot read the trust file"),
        ({"format": "other"}, "is not synapse-signature-trust-v1"),
        ({"format": "synapse-signature-trust-v1", "keys": []}, "names no keys"),
        ({"format": "synapse-signature-trust-v1", "keys": [1]}, "not an object"),
        ({"format": "synapse-signature-trust-v1", "keys": [{}]}, "names no suite"),
        ({"format": "synapse-signature-trust-v1", "keys": [{"suite": "rsa"}]}, "unknown"),
        (
            {
                "format": "synapse-signature-trust-v1",
                "keys": [
                    {
                        "suite": "ed25519",
                        "key_id": "66687aadf862bd77",
                        "public_key": b64(bytes(32)),
                        "log_ids": [],
                    }
                ],
            },
            "log_ids must be a non-empty list",
        ),
        (
            {
                "format": "synapse-signature-trust-v1",
                "keys": [{"suite": "ed25519", "key_id": "0" * 16, "public_key": b64(b"\x01" * 32)}],
            },
            "not derived from its public key",
        ),
    ],
)
def test_a_malformed_trust_file_is_refused(tmp_path: Path, document: object, message: str) -> None:
    path = tmp_path / "trust.json"
    if document is not None:
        path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SignatureSuiteError, match=message):
        load_trust(path)


def test_the_published_key_matches_its_seeds() -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    key = signature_suite_vectors.hybrid_key()
    assert bytes.fromhex(document["ed25519_seed_hex"]) == key.ed25519_seed
    assert bytes.fromhex(document["ml_dsa_seed_hex"]) == key.ml_dsa_seed
    assert base64.b16encode(key.public_bytes()[:32]).decode().lower() in json.dumps(document)


def test_every_supported_lane_provides_ml_dsa() -> None:
    """The hybrid suite is a supported feature, so every CI lane must offer ML-DSA-65."""
    assert signature_suite.ML_DSA_AVAILABLE is True


def test_a_backend_that_rejects_ml_dsa_still_imports_the_cli(tmp_path: Path) -> None:
    """``UnsupportedAlgorithm`` at the probe leaves every command importable."""
    script = tmp_path / "probe.py"
    script.write_text(
        "from cryptography.exceptions import UnsupportedAlgorithm\n"
        "from cryptography.hazmat.primitives.asymmetric import mldsa\n"
        "def refuse(*_args):\n"
        "    raise UnsupportedAlgorithm('ML-DSA is not supported by this backend')\n"
        "mldsa.MLDSA65PrivateKey.from_seed_bytes = staticmethod(refuse)\n"
        "import synapse_channel.cli\n"
        "from synapse_channel.core import signature_suite as s\n"
        "print('available', s.ML_DSA_AVAILABLE)\n"
        "try:\n"
        "    s.generate_key(s.SUITE_HYBRID)\n"
        "except s.SignatureSuiteError as exc:\n"
        "    print('refused', exc)\n",
        encoding="utf-8",
    )
    import subprocess

    completed = subprocess.run(  # noqa: S603 - this interpreter, a script the test wrote
        [sys.executable, str(script)], capture_output=True, text=True, timeout=120, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert "available False" in completed.stdout
    assert "refused ed25519+ml-dsa-65/v1 needs ML-DSA-65" in completed.stdout


def test_the_composite_label_is_not_a_domain() -> None:
    label = COMPOSITE_LABEL.decode("ascii")
    for suite in (SUITE_ED25519, SUITE_HYBRID):
        with pytest.raises(SignatureSuiteError, match="composite label"):
            preimage(suite, label, b"m")
