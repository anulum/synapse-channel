# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — signed, versioned policies with real keys and files
"""Sign, verify and bind policies with real Ed25519 keys, files and the CLI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli
from synapse_channel.core.frame_canonical import frame_canonical_json
from synapse_channel.core.identity_keys import (
    generate_signing_key,
    public_key_b64,
    write_signing_key,
)
from synapse_channel.core.policy_signing import (
    PolicySigningError,
    canonical_policy_signed_bytes,
    load_policy_document,
    policy_digest,
    policy_provenance,
    sign_policy_document,
    verify_policy_document,
)

_POLICY: dict[str, Any] = {
    "version": 1,
    "policy_version": 3,
    "mode": "enforcement",
    "rules": {"required_tests": {"commands": ["pytest"]}},
}


def _keys(tmp_path: Path, *, revoked: bool = False) -> tuple[Any, Path, Path]:
    """Return a private key, its PEM file and a trust bundle naming it as ``k1``."""
    private_key = generate_signing_key()
    pem = tmp_path / "policy.pem"
    write_signing_key(pem, private_key)
    entry: dict[str, Any] = {"key_id": "k1", "public_key": public_key_b64(private_key)}
    if revoked:
        entry["revoked"] = True
    bundle = tmp_path / "trust.json"
    bundle.write_text(json.dumps({"version": 1, "keys": [entry]}), encoding="utf-8")
    return private_key, pem, bundle


def _trust(bundle: Path) -> dict[str, Any]:
    loaded = json.loads(bundle.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _write(path: Path, document: dict[str, Any]) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _receipt(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "receipt.json",
        {"task_id": "T1", "owner": "alice", "evidence": ["pytest -q passed"], "changed_files": []},
    )


def test_a_signed_policy_verifies_and_names_its_key_version_and_digest(tmp_path: Path) -> None:
    private_key, _, bundle = _keys(tmp_path)
    signed = sign_policy_document(_POLICY, private_key=private_key, key_id="k1")

    assert verify_policy_document(signed, _trust(bundle)) == "k1"
    provenance = policy_provenance(signed, trust_document=_trust(bundle), min_policy_version=3)
    assert provenance.as_dict() == {
        "canonical": "rfc8785",
        "digest": policy_digest(_POLICY),
        "policy_version": 3,
        "key_id": "k1",
        "signed": True,
        "verified": True,
    }
    # The digest is an independent RFC 8785 recomputation over the content.
    expected = "sha256:" + hashlib.sha256(frame_canonical_json(_POLICY)).hexdigest()
    assert policy_digest(signed) == expected
    # The signed bytes bind the envelope but not its value.
    signed_bytes = canonical_policy_signed_bytes(signed)
    assert signed_bytes.startswith(b"SYNAPSE-CHANNEL:POLICY:v1\x00")
    assert b'"key_id":"k1"' in signed_bytes and signed["signature"]["value"].encode() not in (
        signed_bytes
    )


def test_an_unsigned_policy_signs_as_its_whole_canonical_content() -> None:
    """Without an envelope the signed bytes are the domain and the document itself."""
    assert canonical_policy_signed_bytes(_POLICY) == (
        b"SYNAPSE-CHANNEL:POLICY:v1\x00" + frame_canonical_json(_POLICY)
    )


def test_formatting_does_not_change_the_digest(tmp_path: Path) -> None:
    compact = _write(tmp_path / "a.json", _POLICY)
    spaced = tmp_path / "b.json"
    spaced.write_text(json.dumps(dict(reversed(list(_POLICY.items()))), indent=4), "utf-8")

    assert policy_digest(load_policy_document(compact)) == policy_digest(
        load_policy_document(spaced)
    )


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda doc: doc["rules"].update({"extra": {}}), "verification failed"),
        (lambda doc: doc.update({"policy_version": 4}), "verification failed"),
        (lambda doc: doc["signature"].update({"key_id": "k2"}), "'k2' is not trusted"),
        (lambda doc: doc["signature"].update({"algorithm": "rsa"}), "algorithm must be ed25519"),
        (lambda doc: doc["signature"].update({"version": 2}), "signature version must be 1"),
        (lambda doc: doc["signature"].update({"version": True}), "signature version must be 1"),
        (lambda doc: doc["signature"].pop("key_id"), "exactly version, algorithm"),
        (lambda doc: doc["signature"].update({"extra": 1}), "exactly version, algorithm"),
        (lambda doc: doc["signature"].update({"value": "***"}), "not valid base64"),
        (lambda doc: doc["signature"].update({"value": 7}), "must be base64 text"),
        (lambda doc: doc["signature"].update({"key_id": " k1"}), "without surrounding"),
        (lambda doc: doc.pop("policy_version"), "must declare policy_version"),
        (lambda doc: doc.pop("signature"), "the policy is not signed"),
    ],
)
def test_every_altered_policy_or_envelope_is_refused(
    tmp_path: Path, edit: Any, message: str
) -> None:
    private_key, _, bundle = _keys(tmp_path)
    signed = json.loads(
        json.dumps(sign_policy_document(_POLICY, private_key=private_key, key_id="k1"))
    )
    edit(signed)

    with pytest.raises(PolicySigningError, match=message):
        verify_policy_document(signed, _trust(bundle))


def test_a_key_relabelled_to_another_trusted_id_fails_verification(tmp_path: Path) -> None:
    """The key id is signed, so moving a signature to another trusted id fails."""
    private_key, _, bundle = _keys(tmp_path)
    other = generate_signing_key()
    trust = _trust(bundle)
    trust["keys"].append({"key_id": "k2", "public_key": public_key_b64(other)})
    signed = sign_policy_document(_POLICY, private_key=other, key_id="k2")
    signed["signature"]["key_id"] = "k1"

    with pytest.raises(PolicySigningError, match="verification failed"):
        verify_policy_document(signed, trust)
    assert private_key is not other


def test_a_revoked_key_is_not_trusted(tmp_path: Path) -> None:
    private_key, _, bundle = _keys(tmp_path, revoked=True)
    signed = sign_policy_document(_POLICY, private_key=private_key, key_id="k1")

    with pytest.raises(PolicySigningError, match="'k1' is not trusted"):
        verify_policy_document(signed, _trust(bundle))


def test_a_malformed_trust_bundle_is_refused_as_a_policy_bundle(tmp_path: Path) -> None:
    private_key, _, bundle = _keys(tmp_path)
    trust = _trust(bundle)
    trust["keys"].append(dict(trust["keys"][0]))
    signed = sign_policy_document(_POLICY, private_key=private_key, key_id="k1")

    with pytest.raises(PolicySigningError, match="duplicate policy trust key id: k1"):
        verify_policy_document(signed, trust)


@pytest.mark.parametrize(
    ("document", "key_id", "message"),
    [
        ({"version": 1}, "k1", "must declare policy_version"),
        ({"policy_version": 0}, "k1", "integer of at least 1"),
        ({"policy_version": True}, "k1", "integer of at least 1"),
        ({"policy_version": 1}, "", "key_id must be non-empty"),
        ({"policy_version": 1}, "k1 ", "key_id must be non-empty"),
        ({"policy_version": 1, "limit": float("nan")}, "k1", "RFC 8785 canonical JSON cannot"),
    ],
)
def test_signing_refuses_an_unversioned_or_unrepresentable_policy(
    document: dict[str, Any], key_id: str, message: str
) -> None:
    with pytest.raises(PolicySigningError, match=message):
        sign_policy_document(document, private_key=generate_signing_key(), key_id=key_id)


def test_the_version_floor_needs_a_trust_bundle_and_refuses_an_older_policy(
    tmp_path: Path,
) -> None:
    private_key, _, bundle = _keys(tmp_path)
    signed = sign_policy_document(_POLICY, private_key=private_key, key_id="k1")

    with pytest.raises(PolicySigningError, match="needs --policy-trust-bundle"):
        policy_provenance(signed, min_policy_version=1)
    with pytest.raises(
        PolicySigningError, match="policy_version 3 is below the required minimum 4"
    ):
        policy_provenance(signed, trust_document=_trust(bundle), min_policy_version=4)


def test_unchecked_provenance_describes_without_verifying(tmp_path: Path) -> None:
    private_key, _, _ = _keys(tmp_path)
    signed = sign_policy_document(_POLICY, private_key=private_key, key_id="k1")
    signed["signature"]["value"] = "AAAA"  # not checked without a bundle

    described = policy_provenance(signed).as_dict()
    assert (described["signed"], described["verified"], described["key_id"]) == (True, False, "k1")
    unsigned = policy_provenance({"version": 1, "policy_version": "x"}).as_dict()
    assert (unsigned["signed"], unsigned["key_id"], unsigned["policy_version"]) == (
        False,
        None,
        None,
    )
    # A value RFC 8785 cannot represent leaves the unchecked digest empty, not an error.
    assert policy_provenance({"limit": float("inf")}).digest is None


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("policy.toml", "version = 1\n", "only a JSON policy can be signed"),
        ("missing.json", None, "policy file does not exist"),
        ("bad.json", "{", "invalid policy JSON"),
        ("list.json", "[]", "mapping at the top level"),
    ],
)
def test_loading_a_policy_for_signing_refuses_toml_and_bad_files(
    tmp_path: Path, name: str, content: str | None, message: str
) -> None:
    path = tmp_path / name
    if content is not None:
        path.write_text(content, encoding="utf-8")

    with pytest.raises(PolicySigningError, match=message):
        load_policy_document(path)


def test_cli_sign_and_verify_round_trip_without_replacing_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, pem, bundle = _keys(tmp_path)
    source = _write(tmp_path / "policy.json", _POLICY)
    out = tmp_path / "signed.json"

    assert (
        cli.main(
            ["policy", "sign", str(source), "--key", str(pem), "--key-id", "k1", "--out", str(out)]
        )
        == 0
    )
    assert out.stat().st_mode & 0o777 == 0o600
    assert json.loads(source.read_text(encoding="utf-8")) == _POLICY
    assert (
        cli.main(
            ["policy", "sign", str(source), "--key", str(pem), "--key-id", "k1", "--out", str(out)]
        )
        == 2
    )
    assert "refusing to replace existing" in capsys.readouterr().err

    assert cli.main(["policy", "verify", str(out), "--trust-bundle", str(bundle)]) == 0
    assert capsys.readouterr().out.startswith("policy verified: version 3, key k1, sha256:")
    assert cli.main(["policy", "verify", str(out), "--trust-bundle", str(bundle), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["verified"] is True
    assert (
        cli.main(
            [
                "policy",
                "verify",
                str(out),
                "--trust-bundle",
                str(bundle),
                "--min-policy-version",
                "9",
            ]
        )
        == 2
    )
    assert "below the required minimum 9" in capsys.readouterr().err

    assert cli.main(["policy", "sign", str(source), "--key", str(pem), "--key-id", "k1"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["signature"]["key_id"] == "k1"
    assert (
        cli.main(
            ["policy", "sign", str(source), "--key", str(tmp_path / "nokey.pem"), "--key-id", "k1"]
        )
        == 2
    )
    assert capsys.readouterr().err.startswith("policy sign: ")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "trust bundle does not exist"),
        ("{", "invalid policy trust bundle JSON"),
        ("[]", "must be a JSON object"),
    ],
)
def test_cli_verify_refuses_a_missing_or_malformed_trust_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str | None, message: str
) -> None:
    private_key, _, _ = _keys(tmp_path)
    signed = _write(
        tmp_path / "signed.json",
        sign_policy_document(_POLICY, private_key=private_key, key_id="k1"),
    )
    bundle = tmp_path / "bundle.json"
    if content is not None:
        bundle.write_text(content, encoding="utf-8")

    assert cli.main(["policy", "verify", str(signed), "--trust-bundle", str(bundle)]) == 2
    assert message in capsys.readouterr().err


def test_policy_check_with_a_trust_bundle_verifies_before_any_rule(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    private_key, _, bundle = _keys(tmp_path)
    receipt = _receipt(tmp_path)
    unsigned = _write(tmp_path / "unsigned.json", _POLICY)
    signed = _write(
        tmp_path / "signed.json",
        sign_policy_document(_POLICY, private_key=private_key, key_id="k1"),
    )
    base = [
        "policy-check",
        "T1",
        "--receipt-json",
        str(receipt),
        "--policy-trust-bundle",
        str(bundle),
    ]

    assert cli.main([*base, "--policy", str(unsigned), "--json"]) == 2
    refused = capsys.readouterr().out
    assert "the policy is not signed" in refused and "decisions" not in refused

    toml = tmp_path / "policy.toml"
    toml.write_text('version = 1\nmode = "advisory"\n', encoding="utf-8")
    assert cli.main([*base, "--policy", str(toml)]) == 2
    assert "only a JSON policy can be signed or verified" in capsys.readouterr().out

    assert cli.main([*base, "--policy", str(signed), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["policy"] == {
        "canonical": "rfc8785",
        "digest": policy_digest(_POLICY),
        "policy_version": 3,
        "key_id": "k1",
        "signed": True,
        "verified": True,
    }
    assert cli.main([*base, "--policy", str(signed)]) == 0
    assert f"  policy: {policy_digest(_POLICY)} (verified, key k1)" in capsys.readouterr().out


def test_policy_check_without_a_bundle_names_the_policy_and_refuses_a_floor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    private_key, _, _ = _keys(tmp_path)
    receipt = _receipt(tmp_path)
    unsigned = _write(tmp_path / "unsigned.json", _POLICY)
    signed = _write(
        tmp_path / "signed.json",
        sign_policy_document(_POLICY, private_key=private_key, key_id="k1"),
    )
    toml = tmp_path / "dated.toml"
    toml.write_text('version = 1\nmode = "advisory"\nreviewed = 2026-11-06\n', encoding="utf-8")
    base = ["policy-check", "T1", "--receipt-json", str(receipt)]

    assert cli.main([*base, "--policy", str(unsigned), "--json"]) == 0
    policy = json.loads(capsys.readouterr().out)["policy"]
    assert (policy["signed"], policy["verified"], policy["policy_version"]) == (False, False, 3)
    assert cli.main([*base, "--policy", str(unsigned)]) == 0
    assert "(unsigned)" in capsys.readouterr().out
    assert cli.main([*base, "--policy", str(signed)]) == 0
    assert "(signed, not verified)" in capsys.readouterr().out
    assert cli.main([*base, "--policy", str(toml)]) == 0
    assert "  policy: no canonical digest (unsigned)" in capsys.readouterr().out
    assert cli.main([*base, "--policy", str(signed), "--min-policy-version", "1"]) == 2
    assert "--min-policy-version needs --policy-trust-bundle" in capsys.readouterr().out


_COUNT_OPENS = """
import sys
from pathlib import Path

policy = str(Path(sys.argv[1]).resolve())
opens = []


def count(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes)):
        name = args[0].decode() if isinstance(args[0], bytes) else args[0]
        if name and str(Path(name).resolve()) == policy:
            opens.append(name)


sys.addaudithook(count)
from synapse_channel import cli

code = cli.main(sys.argv[2:])
print(f"OPENS={len(opens)} CODE={code}", file=sys.stderr)
"""


@pytest.mark.parametrize("with_bundle", [True, False], ids=["verified", "unchecked"])
def test_policy_check_reads_the_policy_file_exactly_once(tmp_path: Path, with_bundle: bool) -> None:
    """The verified mapping is the evaluated one: the file is opened a single time."""
    import os
    import subprocess
    import sys

    private_key, _, bundle = _keys(tmp_path)
    signed = _write(
        tmp_path / "signed.json",
        sign_policy_document(_POLICY, private_key=private_key, key_id="k1"),
    )
    argv = [
        "policy-check",
        "T1",
        "--policy",
        str(signed),
        "--receipt-json",
        str(_receipt(tmp_path)),
    ]
    if with_bundle:
        argv += ["--policy-trust-bundle", str(bundle)]
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(root / "src"), env.get("PYTHONPATH", "")])
    result = subprocess.run(
        [sys.executable, "-c", _COUNT_OPENS, str(signed), *argv],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )

    assert "OPENS=1 CODE=0" in result.stderr, result.stderr


@pytest.mark.parametrize(
    "text",
    [
        '{"policy_version": 1, "mode": "advisory", "mode": "enforcement"}',
        '{"policy_version": 1, "rules": {"a": {}, "a": {"x": 1}}}',
    ],
    ids=["top-level", "nested"],
)
def test_a_policy_repeating_a_member_name_is_refused(tmp_path: Path, text: str) -> None:
    """Parsers disagree on which repeated name wins, so a signed policy may hold none."""
    path = tmp_path / "dup.json"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(PolicySigningError, match="repeats the member"):
        load_policy_document(path)
