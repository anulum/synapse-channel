# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hybrid countersignatures of AEF tree heads, core and command line
"""Countersign a real hub log's tree head and check it, through the API and the CLI."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli
from synapse_channel.core import signature_suite
from synapse_channel.core.aef_countersign import (
    AefCountersignError,
    CountersignVerdict,
    countersign_sth,
    sth_digest,
    verify_countersignature,
)
from synapse_channel.core.aef_emission import AefReceiptLog
from synapse_channel.core.aef_trust_file import load_aef_trust
from synapse_channel.core.receipt_signing import (
    generate_receipt_signing_key,
    load_receipt_signing_key,
)
from synapse_channel.core.signature_suite import SUITE_ED25519, SUITE_HYBRID, generate_key

_NOW_MS = 1_783_941_000_000


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Path]:
    """Write a real three-receipt hub log, its trust file, export and tree head."""
    key_path = tmp_path / "aef.key"
    generate_receipt_signing_key(key_path)
    db = tmp_path / "hub.db"
    with AefReceiptLog(db, hub_id="hub-a", signing_key=load_receipt_signing_key(key_path)) as log:
        for number in (1, 2, 3):
            log.append(
                receipt_type="lease",
                action="grant",
                actor_id="agent-1",
                subject={"task_id": f"T{number}", "epoch": 1, "lease_expires_at": _NOW_MS + 9},
                issued_at=_NOW_MS - 10,
                decision="allow",
                legacy_seq=number,
            )
    paths = {
        "db": db,
        "trust": tmp_path / "trust.json",
        "receipts": tmp_path / "receipts.jsonl",
        "sth": tmp_path / "sth.json",
        "key": tmp_path / "pq.key",
        "pq_trust": tmp_path / "pq-trust.json",
        "cs": tmp_path / "cs.json",
    }
    public = str(key_path.with_name("aef.key.pub"))
    for argv in (
        ("aef", "trust", "--public-key", public, "--hub-id", "hub-a", "--out", str(paths["trust"])),
        ("aef", "export", str(db), "--out", str(paths["receipts"])),
        (
            "aef",
            "sth",
            str(db),
            "--hub-id",
            "hub-a",
            "--signing-key",
            str(key_path),
            "--out",
            str(paths["sth"]),
        ),
        ("aef", "pq-key", "new", "--out", str(paths["key"]), "--trust-out", str(paths["pq_trust"])),
    ):
        assert _run(capsys, *argv)[0] == 0, argv
    return paths


def _verify(paths: dict[str, Path], *extra: str) -> list[str]:
    return [
        "aef",
        "verify",
        str(paths["receipts"]),
        "--trust",
        str(paths["trust"]),
        "--now-ms",
        str(_NOW_MS),
        "--sth",
        str(paths["sth"]),
        *extra,
    ]


def _json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def test_a_countersigned_complete_export_passes_the_strict_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _log(tmp_path, capsys)
    code, _out, err = _run(
        capsys,
        "aef",
        "countersign",
        "--sth",
        str(paths["sth"]),
        "--trust",
        str(paths["trust"]),
        "--key",
        str(paths["key"]),
        "--out",
        str(paths["cs"]),
    )
    assert code == 0 and "tree head of size 3" in err
    strict = (
        "--countersignature",
        str(paths["cs"]),
        "--countersign-trust",
        str(paths["pq_trust"]),
        "--require-countersignature",
    )
    code, out, _err = _run(capsys, *_verify(paths, *strict))
    assert code == 0
    assert "countersignature VALID" in out and "sth=STH_COMPLETE" in out
    code, out, _err = _run(capsys, *_verify(paths, *strict, "--json"))
    report = json.loads(out)["countersignatures"]
    assert code == 0 and report[0]["verdict"] == "VALID"
    assert report[0]["sth_verdict"] == "STH_COMPLETE"
    # Without the strict flag a countersignature only reports.
    code, out, _err = _run(capsys, *_verify(paths, *strict[:-1]))
    assert code == 0


def test_a_cut_export_fails_the_strict_check_even_with_a_valid_countersignature(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _log(tmp_path, capsys)
    assert (
        _run(
            capsys,
            "aef",
            "countersign",
            "--sth",
            str(paths["sth"]),
            "--trust",
            str(paths["trust"]),
            "--key",
            str(paths["key"]),
            "--out",
            str(paths["cs"]),
        )[0]
        == 0
    )
    lines = paths["receipts"].read_text(encoding="utf-8").splitlines(keepends=True)
    paths["receipts"].write_text("".join(lines[:2]), encoding="utf-8")
    code, out, _err = _run(
        capsys,
        *_verify(
            paths,
            "--countersignature",
            str(paths["cs"]),
            "--countersign-trust",
            str(paths["pq_trust"]),
            "--require-countersignature",
        ),
    )
    assert code == 1
    assert "countersignature VALID" in out and "sth=STH_TRUNCATED" in out


def test_a_countersignature_of_no_given_head_is_a_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _log(tmp_path, capsys)
    document = countersign_sth(
        _json(paths["sth"]),
        trust_store=load_aef_trust(paths["trust"]),
        key=signature_suite.load_key(paths["key"], suite=SUITE_HYBRID),
    )
    document["sth_sha256"] = "0" * 64
    paths["cs"].write_text(json.dumps(document), encoding="utf-8")
    code, out, _err = _run(
        capsys,
        *_verify(
            paths,
            "--countersignature",
            str(paths["cs"]),
            "--countersign-trust",
            str(paths["pq_trust"]),
            "--require-countersignature",
        ),
    )
    # Changing the bound digest also breaks the signature over it.
    assert code == 1 and "countersignature INVALID" in out and "sth=NO_HEAD" in out


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (("--require-countersignature",), "needs at least one --countersignature"),
        (("--countersignature", "cs.json"), "needs --countersign-trust"),
    ],
)
def test_unusable_countersignature_options_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: tuple[str, ...], message: str
) -> None:
    paths = _log(tmp_path, capsys)
    code, _out, err = _run(capsys, *_verify(paths, *extra))
    assert code == 2 and message in err
    no_head = [
        "aef",
        "verify",
        str(paths["receipts"]),
        "--trust",
        str(paths["trust"]),
        "--countersignature",
        "cs.json",
        "--countersign-trust",
        "t.json",
    ]
    code, _out, err = _run(capsys, *no_head)
    assert code == 2 and "needs the --sth head" in err


def test_unreadable_countersignature_inputs_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _log(tmp_path, capsys)
    missing = (
        "--countersignature",
        str(tmp_path / "absent.json"),
        "--countersign-trust",
        str(paths["pq_trust"]),
    )
    assert _run(capsys, *_verify(paths, *missing))[0] == 2
    (tmp_path / "array.json").write_text("[]", encoding="utf-8")
    not_object = (
        "--countersignature",
        str(tmp_path / "array.json"),
        "--countersign-trust",
        str(paths["pq_trust"]),
    )
    code, _out, err = _run(capsys, *_verify(paths, *not_object))
    assert code == 2 and "is not a JSON object" in err


def test_countersign_refuses_what_it_cannot_vouch_for(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _log(tmp_path, capsys)
    sth = _json(paths["sth"])
    sth["root"] = "0" * 64
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(sth), encoding="utf-8")
    base = ["aef", "countersign", "--trust", str(paths["trust"]), "--key", str(paths["key"])]
    code, _out, err = _run(capsys, *base, "--sth", str(forged))
    assert code == 2 and "does not verify" in err
    code, out, _err = _run(capsys, *base, "--sth", str(paths["sth"]))
    assert code == 0 and json.loads(out)["signature"]["alg"] == SUITE_HYBRID
    paths["cs"].write_text("taken", encoding="utf-8")
    code, _out, err = _run(capsys, *base, "--sth", str(paths["sth"]), "--out", str(paths["cs"]))
    assert code == 2 and "cannot write" in err
    with pytest.raises(AefCountersignError, match="uses ed25519\\+ml-dsa-65/v1"):
        countersign_sth(
            _json(paths["sth"]),
            trust_store=load_aef_trust(paths["trust"]),
            key=generate_key(SUITE_ED25519),
        )


def test_pq_key_new_refuses_to_overwrite_and_needs_ml_dsa(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    taken = tmp_path / "taken.key"
    taken.write_text("x", encoding="utf-8")
    argv = ["aef", "pq-key", "new", "--out", str(taken), "--trust-out", str(tmp_path / "t.json")]
    code, _out, err = _run(capsys, *argv)
    assert code == 2 and "cannot write" in err
    monkeypatch.setattr(signature_suite, "ML_DSA_AVAILABLE", False)
    argv[4] = str(tmp_path / "new.key")
    code, _out, err = _run(capsys, *argv)
    assert code == 2 and "needs ML-DSA-65" in err


def test_each_verdict_of_a_countersignature(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _log(tmp_path, capsys)
    sth = _json(paths["sth"])
    key = signature_suite.load_key(paths["key"], suite=SUITE_HYBRID)
    trust = signature_suite.load_trust(paths["pq_trust"])
    document: dict[str, Any] = countersign_sth(
        sth, trust_store=load_aef_trust(paths["trust"]), key=key
    )
    assert verify_countersignature(document, sth, trust=trust).verdict is CountersignVerdict.VALID

    def verdict(doc: dict[str, Any], head: dict[str, Any] = sth, **kwargs: Any) -> str:
        return verify_countersignature(doc, head, trust=kwargs.get("trust", trust)).verdict.value

    malformed: list[dict[str, Any]] = []
    for change in ("extra", "format", "alg", "envelope", "key_id"):
        broken = copy.deepcopy(document)
        if change == "extra":
            broken["note"] = "x"
        elif change == "format":
            broken["format"] = "other"
        elif change == "alg":
            broken["signature"]["alg"] = SUITE_ED25519
        elif change == "envelope":
            broken["signature"] = "value"
        else:
            broken["signature"]["key_id"] = 7
        malformed.append(broken)
    assert {verdict(doc) for doc in malformed} == {"INVALID"}
    assert verdict(document, trust={}) == "UNTRUSTED"
    as_ed = {key.key_id(): (SUITE_ED25519, key.public_bytes())}
    assert verdict(document, trust=as_ed) == "UNTRUSTED"
    flipped = copy.deepcopy(document)
    value = flipped["signature"]["value"]
    flipped["signature"]["value"] = ("A" if value[0] != "A" else "B") + value[1:]
    assert verdict(flipped) == "INVALID"
    not_base64 = copy.deepcopy(document)
    not_base64["signature"]["value"] = "@@@"
    assert verdict(not_base64) == "INVALID"
    # A head that differs only in its own Ed25519 signature bytes is another head.
    resigned = copy.deepcopy(sth)
    head_value = resigned["signature"]["value"]
    resigned["signature"]["value"] = ("A" if head_value[0] != "A" else "B") + head_value[1:]
    assert sth_digest(resigned) != sth_digest(sth)
    assert verdict(document, resigned) == "MISMATCH"
    other_root = dict(sth, root="0" * 64)
    assert verdict(document, other_root) == "MISMATCH"
    assert verdict(document, dict(sth, timestamp=float("nan"))) == "MISMATCH"
