# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — qualification evidence that is itself trustworthy
"""Production runs need a signed, bound, dated and complete qualification bundle.

Real Ed25519 keys sign real bundles; real catalogue files are read by the real
``synapse ops`` command. The operator's own qualification stays enough for a
personal profile, and the verdict labels it as such.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from synapse_channel.cli import main
from synapse_channel.operations.catalogue import Catalogue, LegacyInventory
from synapse_channel.operations.qualification_bundle import (
    MAX_VALIDITY_SECONDS,
    SCHEMA,
    QualificationBundleError,
    gate,
    sign_bundle,
    validate_bundle,
    verify_envelope,
)
from synapse_channel.operations.row import OperationRow
from test_operations_row import list_sessions, qualified
from test_operations_spec import queue_message

NOW = 1_800_000_000
DIGEST = "sha256:" + "a" * 64


def _raw_public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _body(row: OperationRow, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": SCHEMA,
        "operation": row.operation,
        "transport": row.transport,
        "variant": row.variant,
        "binding_digest": row.binding_digest,
        "level": row.qualification,
        "tests": ["tests/test_kimi_sessions.py::test_list"],
        "configuration_sha256": DIGEST,
        "artefacts": {"kimi-cli-2.1.1.whl": DIGEST},
        "environment": {"interpreter": "cpython-3.12.3", "plugin:none": "none"},
        "qualifier": "qualification-lab",
        "issued_at": NOW - 10,
        "valid_until": NOW + 3600,
    }
    body.update(changes)
    return body


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def _production(
    row: OperationRow, envelope: dict[str, Any] | None, key: Ed25519PrivateKey, **kw: Any
) -> Any:
    kw.setdefault("required_environment", ("interpreter",))
    return gate(
        row,
        profile="production",
        now=NOW,
        envelope=envelope,
        trusted_keys={"lab-1": _raw_public(key)},
        **kw,
    )


def test_a_signed_bundle_for_a_read_only_row_lets_it_run_in_production(
    key: Ed25519PrivateKey,
) -> None:
    row = qualified(list_sessions())
    envelope = sign_bundle(_body(row), key, "lab-1")

    verdict = _production(row, envelope, key, required_environment=("interpreter",))

    assert verdict.runnable
    assert verdict.basis == "signed_qualification_bundle by qualification-lab"
    assert verdict.to_json()["reasons"] == []


def test_run_once_does_not_qualify_a_risky_effect_but_accepted_does(
    key: Ed25519PrivateKey,
) -> None:
    once = qualified(list_sessions(effects=("read", "change")))
    accepted = qualified(list_sessions(effects=("read", "change")), level="accepted")

    refused = _production(once, sign_bundle(_body(once), key, "lab-1"), key)
    allowed = _production(accepted, sign_bundle(_body(accepted), key, "lab-1"), key)

    assert refused.reasons == ("run_once does not qualify an effect beyond read in production",)
    assert allowed.runnable


def test_every_mismatch_between_bundle_and_row_is_a_reason(key: Ed25519PrivateKey) -> None:
    row = qualified(list_sessions())
    envelope = sign_bundle(
        _body(
            row,
            operation="session.search",
            binding_digest=DIGEST,
            level="accepted",
            issued_at=NOW + 10,
            valid_until=NOW + 20,
        ),
        key,
        "lab-1",
    )

    verdict = _production(row, envelope, key, required_environment=("interpreter", "gpu-driver"))

    assert not verdict.runnable
    assert verdict.reasons == (
        "the bundle names another operation, transport or variant",
        "the bundle qualified another binding than the row has now",
        "the bundle level differs from the row's qualification",
        "the bundle is not valid at this time",
        "the bundle does not cover the environment: gpu-driver",
    )


def test_an_unqualified_or_stale_row_never_runs(key: Ed25519PrivateKey) -> None:
    row = qualified(list_sessions())
    stale = replace(row, reserved_natives=())

    production = _production(stale, sign_bundle(_body(row), key, "lab-1"), key)
    personal = gate(list_sessions(), profile="personal", now=NOW)

    assert "the row is not qualified for its present binding" in production.reasons
    assert not personal.runnable
    assert personal.basis == "operator_confirmation (personal profile)"


def test_the_personal_profile_rests_on_the_operator_and_says_so() -> None:
    verdict = gate(qualified(list_sessions()), profile="personal", now=NOW)

    assert verdict.runnable
    assert verdict.basis == "operator_confirmation (personal profile)"


def test_production_refuses_missing_untrusted_and_tampered_bundles(
    key: Ed25519PrivateKey,
) -> None:
    row = qualified(list_sessions())
    envelope = sign_bundle(_body(row), key, "lab-1")
    tampered = {**envelope, "bundle": {**envelope["bundle"], "qualifier": "someone-else"}}
    garbled = {**envelope, "signature": "not base64!"}

    missing = _production(row, None, key)
    untrusted = gate(
        row, profile="production", now=NOW, envelope=envelope, required_environment=("x",)
    )
    forged = _production(row, tampered, key)
    unreadable = _production(row, garbled, key)

    assert missing.reasons == ("production needs a signed qualification bundle",)
    assert untrusted.reasons == ("the bundle is signed by a key that is not trusted",)
    assert forged.reasons == ("the bundle signature does not verify",)
    assert unreadable.reasons == ("the bundle signature does not verify",)


def test_an_unknown_profile_is_refused() -> None:
    with pytest.raises(ValueError, match="a profile is one of"):
        gate(list_sessions(), profile="team", now=NOW)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema": "v0"}, "is a synapse-qualification-bundle.v1"),
        ({"extra": 1}, "exactly the fields"),
        ({"qualifier": ""}, "printable text"),
        ({"variant": "line\nbreak"}, "printable text"),
        ({"binding_digest": "abc"}, "sha256:<64 hex>"),
        ({"level": "help_text"}, "level is one of"),
        ({"tests": []}, "1 to 64 tests"),
        ({"tests": [5]}, "printable text"),
        ({"artefacts": {}}, "1 to 64 entries"),
        ({"artefacts": {"x": "sha256:short"}}, "sha256:<64 hex> digests"),
        ({"environment": "cpython"}, "1 to 64 entries"),
        ({"issued_at": NOW, "valid_until": NOW}, "at most 366 days apart"),
        ({"valid_until": NOW + MAX_VALIDITY_SECONDS + 1}, "at most 366 days apart"),
        ({"issued_at": float(NOW)}, "at most 366 days apart"),
    ],
)
def test_malformed_bundles_are_refused(changes: dict[str, Any], message: str) -> None:
    body = _body(qualified(list_sessions()), **changes)
    if "extra" not in changes:
        body.pop("extra", None)

    with pytest.raises(QualificationBundleError, match=message):
        validate_bundle(body)


def test_malformed_envelopes_are_refused(key: Ed25519PrivateKey) -> None:
    with pytest.raises(QualificationBundleError, match="exactly bundle, key_id and signature"):
        verify_envelope({"bundle": {}}, {})
    not_a_mapping: Any = ["not", "a", "mapping"]
    with pytest.raises(QualificationBundleError, match="exactly the fields"):
        validate_bundle(not_a_mapping)


def _catalogue(tmp_path: Path, *rows: OperationRow) -> Path:
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(Catalogue(list(rows)).to_json()), encoding="utf-8")
    return path


def _pem(tmp_path: Path, key: Any, *, mode: int = 0o600) -> Path:
    path = tmp_path / "qualifier.pem"
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(mode)
    return path


def test_the_cli_signs_a_bundle_and_gates_rows_by_profile(
    tmp_path: Path, key: Ed25519PrivateKey, capsys: pytest.CaptureFixture[str]
) -> None:
    """``synapse ops sign-qualification`` then ``synapse ops gate`` over real files."""
    row = qualified(list_sessions())
    risky = qualified(list_sessions(operation="session.search", effects=("read", "change")))
    catalogue = str(_catalogue(tmp_path, row, risky))
    now = int(time.time())
    body = tmp_path / "body.json"
    body.write_text(
        json.dumps(_body(row, issued_at=now - 60, valid_until=now + 3600)),
        encoding="utf-8",
    )
    envelope = tmp_path / "envelope.json"
    trust = tmp_path / "trust.json"
    trust.write_text(
        json.dumps({"keys": {"lab-1": base64.b64encode(_raw_public(key)).decode()}}), "utf-8"
    )
    pem = str(_pem(tmp_path, key))

    assert (
        main(
            [
                "ops",
                "sign-qualification",
                "--bundle-body",
                str(body),
                "--key-file",
                pem,
                "--key-id",
                "lab-1",
                "--out",
                str(envelope),
            ]
        )
        == 0
    )
    production = [
        "ops",
        "gate",
        "--catalogue",
        catalogue,
        "--profile",
        "production",
        "--bundle",
        str(envelope),
        "--trust",
        str(trust),
        "--require-environment",
        "interpreter",
    ]
    assert main([*production, "session.list"]) == 0
    assert json.loads(capsys.readouterr().out)["runnable"] is True
    assert main([*production, "session.search"]) == 1
    reasons = json.loads(capsys.readouterr().out)["reasons"]
    assert "run_once does not qualify an effect beyond read in production" in reasons
    assert (
        main(["ops", "gate", "--catalogue", catalogue, "--profile", "personal", "session.search"])
        == 0
    )
    assert "personal profile" in capsys.readouterr().out
    assert (
        main(["ops", "gate", "--catalogue", catalogue, "--profile", "personal", "session.none"])
        == 2
    )
    assert "0 rows match" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("trust", "message"),
    [
        ("{", "is not JSON"),
        (json.dumps({"keys": {}}), "maps key ids"),
        (json.dumps({"keys": {"k": "not base64!"}}), "not base64"),
        (json.dumps({"keys": {"k": base64.b64encode(b"short").decode()}}), "32-byte"),
    ],
)
def test_the_cli_refuses_unusable_trust_files(
    tmp_path: Path, trust: str, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    catalogue = str(_catalogue(tmp_path, qualified(list_sessions())))
    path = tmp_path / "trust.json"
    path.write_text(trust, encoding="utf-8")

    assert (
        main(
            [
                "ops",
                "gate",
                "--catalogue",
                catalogue,
                "--profile",
                "production",
                "--trust",
                str(path),
                "session.list",
            ]
        )
        == 2
    )
    assert message in capsys.readouterr().err


def test_the_cli_refuses_earlier_catalogues_and_bad_signing_input(
    tmp_path: Path, key: Ed25519PrivateKey, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(LegacyInventory([queue_message()]).to_json()), "utf-8")
    assert (
        main(["ops", "gate", "--catalogue", str(legacy), "--profile", "personal", "session.queue"])
        == 2
    )
    assert "earlier schema is never runnable" in capsys.readouterr().err

    body = tmp_path / "body.json"
    body.write_text(json.dumps(_body(qualified(list_sessions()))), encoding="utf-8")
    out = str(tmp_path / "envelope.json")
    sign = [
        "ops",
        "sign-qualification",
        "--bundle-body",
        str(body),
        "--key-id",
        "lab-1",
        "--out",
        out,
        "--key-file",
    ]
    assert main([*sign, str(_pem(tmp_path, key, mode=0o644))]) == 2
    assert main([*sign, str(_pem(tmp_path, X25519PrivateKey.generate()))]) == 2
    assert "no Ed25519 private key" in capsys.readouterr().err
    body.write_text(json.dumps({"schema": SCHEMA}), encoding="utf-8")
    assert main([*sign, str(_pem(tmp_path, key))]) == 2
    assert "exactly the fields" in capsys.readouterr().err


def test_production_without_a_named_environment_is_refused(key: Ed25519PrivateKey) -> None:
    """A bundle from another machine would otherwise pass when no component is required."""
    row = qualified(list_sessions())
    envelope = sign_bundle(_body(row, environment={"interpreter": "other-machine"}), key, "lab-1")

    verdict = _production(row, envelope, key, required_environment=())

    assert verdict.reasons == ("production names the environment components it requires",)
