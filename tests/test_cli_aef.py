# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — K4-AEF-SURFACE: offline `synapse aef` verification
"""An auditor verifies a hub's AEF receipts offline, from files only.

The receipts are written by the real native receipt log with a key from the real
key generator. The normative conformance vectors drive the chain-conflict,
revocation, expiry and inclusion cases. No hub runs in these tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli
from synapse_channel.cli_aef import AefInputError, _read_bounded
from synapse_channel.core.aef_emission import AefReceiptLog
from synapse_channel.core.aef_trust_file import (
    AEF_TRUST_FORMAT,
    AefTrustFileError,
    load_aef_trust,
    parse_aef_trust,
)
from synapse_channel.core.receipt_signing import (
    generate_receipt_signing_key,
    load_receipt_signing_key,
)

_VECTORS = {
    vector["name"]: vector
    for vector in json.loads(
        (Path(__file__).parent / "fixtures" / "aef_receipt_v0_1.json").read_text(encoding="utf-8")
    )["vectors"]
}
_NOW_MS = 1_783_941_000_000


def _hub_log(tmp_path: Path) -> tuple[Path, Path]:
    """Write two real native receipts; return the hub store and the .pub file."""
    key_path = tmp_path / "aef.key"
    generate_receipt_signing_key(key_path)
    db = tmp_path / "hub.db"
    with AefReceiptLog(db, hub_id="hub-a", signing_key=load_receipt_signing_key(key_path)) as log:
        for number in (1, 2):
            log.append(
                receipt_type="lease",
                action="grant",
                actor_id="agent-1",
                subject={"task_id": f"T{number}", "epoch": 1, "lease_expires_at": _NOW_MS + 9},
                issued_at=_NOW_MS - 10,
                decision="allow",
                legacy_seq=number,
            )
    return db, key_path.with_name("aef.key.pub")


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _vector_trust(tmp_path: Path, *, revoked: bool = False) -> Path:
    vector = _VECTORS["v01-valid-lease-grant"]
    key_id, entry = next(iter(vector["trust_store"]["keys"].items()))
    document = {
        "format": AEF_TRUST_FORMAT,
        "keys": {key_id: {"public_key_hex": entry["public_key_hex"], "revoked": revoked}},
        "logs": {vector["receipt"]["log_id"]: key_id},
    }
    path = tmp_path / ("revoked.json" if revoked else "vector-trust.json")
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _write(path: Path, value: Any) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_trust_export_and_verify_a_real_hub_log_offline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, public_key = _hub_log(tmp_path)
    trust = tmp_path / "trust.json"
    receipts = tmp_path / "receipts.jsonl"

    assert (
        _run(
            capsys,
            "aef",
            "trust",
            "--public-key",
            str(public_key),
            "--hub-id",
            "hub-a",
            "--out",
            str(trust),
        )[0]
        == 0
    )
    assert _run(capsys, "aef", "export", str(db), "--out", str(receipts))[0] == 0
    code, out, _ = _run(
        capsys,
        "aef",
        "verify",
        str(receipts),
        "--trust",
        str(trust),
        "--now-ms",
        str(_NOW_MS),
        "--json",
    )
    report = json.loads(out)

    assert code == 0
    assert report["valid"] == 2
    assert [item["seq"] for item in report["receipts"]] == [1, 2]
    assert {item["verdict"] for item in report["receipts"]} == {"VALID"}
    stdout_code, printed, _ = _run(capsys, "aef", "export", str(db))
    assert stdout_code == 0
    assert printed.encode("utf-8") == receipts.read_bytes()


def test_a_tampered_or_replayed_receipt_and_a_revoked_key_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, public_key = _hub_log(tmp_path)
    trust = tmp_path / "trust.json"
    _run(
        capsys,
        "aef",
        "trust",
        "--public-key",
        str(public_key),
        "--hub-id",
        "hub-a",
        "--out",
        str(trust),
    )
    _run(capsys, "aef", "export", str(db), "--out", str(tmp_path / "all.jsonl"))
    first, second = (json.loads(line) for line in (tmp_path / "all.jsonl").read_text().splitlines())
    tampered = {**second, "actor": {"agent_id": "agent-9"}}
    batch = _write(tmp_path / "batch.json", [first, first, tampered])

    code, out, _ = _run(
        capsys, "aef", "verify", str(batch), "--trust", str(trust), "--now-ms", str(_NOW_MS)
    )
    lines = out.splitlines()
    assert code == 1
    assert lines[0].startswith("seq=1 VALID ")
    assert lines[1].startswith("seq=1 REPLAYED ")
    assert lines[2].startswith("seq=2 INVALID_RECEIPT_ID ")
    assert lines[3] == f"1 of 3 receipts VALID at now_ms={_NOW_MS}"

    document = json.loads(trust.read_text())
    for entry in document["keys"].values():
        entry["revoked"] = True
    revoked = _write(tmp_path / "revoked-trust.json", document)
    code, out, _ = _run(
        capsys,
        "aef",
        "verify",
        str(tmp_path / "all.jsonl"),
        "--trust",
        str(revoked),
        "--now-ms",
        str(_NOW_MS),
    )
    assert code == 1
    assert out.count("REVOKED_KEY") == 2


@pytest.mark.parametrize(
    ("names", "trust_revoked", "expected"),
    [
        (("v01-valid-lease-grant", "v05-replayed-seq"), False, ["VALID", "CHAIN_CONFLICT"]),
        (("v10-revoked-key",), True, ["REVOKED_KEY"]),
        (("v04-expired-receipt",), False, ["EXPIRED"]),
        (
            ("v02-bad-signature", "v03-wrong-domain", "v09-unknown-type"),
            False,
            ["INVALID_SIGNATURE", "INVALID_DOMAIN", "UNVERIFIABLE_TYPE"],
        ),
    ],
)
def test_the_conformance_vectors_through_the_command(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    names: tuple[str, ...],
    trust_revoked: bool,
    expected: list[str],
) -> None:
    receipts = _write(tmp_path / "vectors.json", [_VECTORS[name]["receipt"] for name in names])
    code, out, _ = _run(
        capsys,
        "aef",
        "verify",
        str(receipts),
        "--trust",
        str(_vector_trust(tmp_path, revoked=trust_revoked)),
        "--now-ms",
        str(_NOW_MS),
        "--json",
    )
    assert [item["verdict"] for item in json.loads(out)["receipts"]] == expected
    assert code == (0 if expected == ["VALID"] else 1)


@pytest.mark.parametrize(
    ("name", "expected", "code"),
    [("v06-inclusion-pass", "INCLUSION_VALID", 0), ("v07-inclusion-fail", "INCLUSION_INVALID", 1)],
)
def test_inclusion_against_a_signed_tree_head(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str, expected: str, code: int
) -> None:
    vector = _VECTORS[name]
    result = _run(
        capsys,
        "aef",
        "inclusion",
        "--trust",
        str(_vector_trust(tmp_path)),
        "--receipt",
        str(_write(tmp_path / "r.json", vector["receipt"])),
        "--sth",
        str(_write(tmp_path / "sth.json", vector["inclusion"]["sth"])),
        "--proof",
        str(_write(tmp_path / "proof.json", vector["inclusion"]["proof"])),
    )
    assert result[:2] == (code, f"{expected}\n")


def test_unreadable_inputs_exit_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    trust = _vector_trust(tmp_path)
    receipt = _write(tmp_path / "r.json", _VECTORS["v01-valid-lease-grant"]["receipt"])
    bad_line = tmp_path / "bad.jsonl"
    bad_line.write_text('{"a": 1}\n\n[1]\n', encoding="utf-8")
    not_json = tmp_path / "not.json"
    not_json.write_text("{", encoding="utf-8")
    cases = [
        ("aef", "verify", str(tmp_path / "missing.json"), "--trust", str(trust)),
        ("aef", "verify", str(not_json), "--trust", str(trust)),
        ("aef", "verify", str(bad_line), "--trust", str(trust)),
        ("aef", "verify", str(_write(tmp_path / "items.json", [1])), "--trust", str(trust)),
        ("aef", "verify", str(receipt), "--trust", str(not_json)),
        (
            "aef",
            "inclusion",
            "--trust",
            str(trust),
            "--receipt",
            str(receipt),
            "--sth",
            str(_write(tmp_path / "list.json", [])),
            "--proof",
            str(receipt),
        ),
        (
            "aef",
            "inclusion",
            "--trust",
            str(trust),
            "--receipt",
            str(receipt),
            "--sth",
            str(not_json),
            "--proof",
            str(receipt),
        ),
        ("aef", "trust", "--public-key", str(not_json), "--hub-id", "hub-a"),
        ("aef", "trust", "--public-key", str(tmp_path / "nope.pub"), "--hub-id", "hub-a"),
    ]
    for argv in cases:
        code, _out, err = _run(capsys, *argv)
        assert code == 2, argv
        assert err.startswith("error: "), argv


def test_an_empty_receipt_set_is_not_a_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "none.jsonl"
    empty.write_text("", encoding="utf-8")
    code, out, _ = _run(
        capsys, "aef", "verify", str(empty), "--trust", str(_vector_trust(tmp_path))
    )
    assert code == 1
    assert out.startswith("0 of 0 receipts VALID at now_ms=")


def test_export_and_trust_refuse_to_overwrite_or_read_a_store_without_receipts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, public_key = _hub_log(tmp_path)
    existing = tmp_path / "existing.json"
    existing.write_text("keep", encoding="utf-8")
    plain = tmp_path / "plain.db"
    plain.write_bytes(b"")
    cases = [
        ("aef", "export", str(tmp_path / "absent.db")),
        ("aef", "export", str(plain)),
        ("aef", "export", str(db), "--out", str(existing)),
        ("aef", "export", str(db), "--db-key-file", str(tmp_path / "absent.key")),
        (
            "aef",
            "trust",
            "--public-key",
            str(public_key),
            "--hub-id",
            "hub-a",
            "--out",
            str(existing),
        ),
    ]
    for argv in cases:
        code, _out, err = _run(capsys, *argv)
        assert code == 2, argv
        assert err.startswith("error: "), argv
    assert existing.read_text(encoding="utf-8") == "keep"
    code, out, _ = _run(
        capsys, "aef", "trust", "--public-key", str(public_key), "--hub-id", "hub-a"
    )
    assert code == 0
    assert json.loads(out)["format"] == AEF_TRUST_FORMAT


def test_the_read_bound_refuses_an_oversized_document(tmp_path: Path) -> None:
    big = tmp_path / "big.json"
    big.write_bytes(b"x" * 11)
    with pytest.raises(AefInputError, match="exceeds 10 bytes"):
        _read_bounded(str(big), limit=10)


def _trust_doc(**overrides: Any) -> dict[str, Any]:
    vector = _VECTORS["v01-valid-lease-grant"]
    key_id, entry = next(iter(vector["trust_store"]["keys"].items()))
    document: dict[str, Any] = {
        "format": AEF_TRUST_FORMAT,
        "keys": {key_id: {"public_key_hex": entry["public_key_hex"]}},
        "logs": {vector["receipt"]["log_id"]: key_id},
    }
    document.update(overrides)
    return document


def _key_entry(**fields: Any) -> dict[str, Any]:
    document = _trust_doc()
    key_id = next(iter(document["keys"]))
    document["keys"][key_id].update(fields)
    return document


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"\xff", "not UTF-8 JSON"),
        (b'{"format": "a", "format": "b"}', "repeats the field 'format'"),
        (json.dumps([]).encode(), "document must be an object"),
        (json.dumps(_trust_doc(extra=1)).encode(), "unknown fields: extra"),
        (json.dumps(_trust_doc(format="aef-trust-v9")).encode(), "format must be"),
        (json.dumps({"format": AEF_TRUST_FORMAT, "keys": {}}).encode(), "missing fields: logs"),
        (json.dumps(_trust_doc(keys=[])).encode(), "keys and logs must be objects"),
        (json.dumps(_trust_doc(logs={"a" * 64: 5})).encode(), "map a log id to a key id"),
        (json.dumps(_trust_doc(logs={"a" * 64: "0" * 16})).encode(), "inconsistent"),
        (json.dumps(_key_entry(public_key_hex=None)).encode(), "needs public_key_hex"),
        (json.dumps(_key_entry(public_key_hex="zz")).encode(), "is not hex"),
        (json.dumps(_key_entry(public_key_hex="00")).encode(), "32 raw Ed25519 bytes"),
        (json.dumps(_key_entry(revoked="no")).encode(), "revoked must be true or false"),
        (json.dumps(_key_entry(senders="agent-7")).encode(), "senders must be a list"),
        (json.dumps(_key_entry(not_before=True)).encode(), "integer milliseconds"),
        (json.dumps(_key_entry(not_before=5, not_after=4)).encode(), "window is inverted"),
        (json.dumps(_key_entry(label="x")).encode(), "unknown fields: label"),
        (json.dumps(_key_entry(senders=["agent-7"], not_before=1, not_after=2)).encode(), None),
    ],
)
def test_the_trust_file_contract_is_strict(raw: bytes, message: str | None) -> None:
    if message is None:
        store = parse_aef_trust(raw)
        key = next(iter(store.keys.values()))
        assert (key.senders, key.not_before, key.not_after) == (frozenset({"agent-7"}), 1, 2)
        return
    with pytest.raises(AefTrustFileError, match=message):
        parse_aef_trust(raw)


def test_the_trust_file_loader_bounds_and_reports_the_file(tmp_path: Path) -> None:
    with pytest.raises(AefTrustFileError, match="cannot read AEF trust file"):
        load_aef_trust(tmp_path / "absent.json")
    big = tmp_path / "big.json"
    big.write_bytes(b" " * ((1 << 20) + 1))
    with pytest.raises(AefTrustFileError, match="exceeds"):
        load_aef_trust(big)
