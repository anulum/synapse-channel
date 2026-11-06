# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — AEF signed tree heads on real stores, keys and vectors
"""Issue AEF tree heads from real hub stores and check exports against them."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from synapse_channel import cli
from synapse_channel.core.aef_emission import AefEmissionError, AefReceiptLog
from synapse_channel.core.aef_sth import AefSthVerdict, sign_aef_sth, verify_aef_sth
from synapse_channel.core.aef_trust_file import parse_aef_trust
from synapse_channel.core.receipt_signing import (
    ReceiptSigningKey,
    generate_receipt_signing_key,
    load_receipt_signing_key,
)

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = json.loads((_ROOT / "tests" / "fixtures" / "aef_sth_v0_1.json").read_text("utf-8"))
_NOW_MS = 1_783_941_000_000


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "aef_sth_vectors", _ROOT / "tools" / "aef_sth_vectors.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["aef_sth_vectors"] = module
    spec.loader.exec_module(module)
    return module


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _append(log: AefReceiptLog, numbers: range) -> None:
    for number in numbers:
        log.append(
            receipt_type="lease",
            action="grant",
            actor_id="agent-1",
            subject={"task_id": f"T{number}", "epoch": 1, "lease_expires_at": _NOW_MS + 9},
            issued_at=_NOW_MS - 10,
            decision="allow",
            legacy_seq=number,
        )


def _hub(tmp_path: Path, count: int = 3) -> tuple[Path, Path, ReceiptSigningKey]:
    """Write ``count`` real native receipts; return the store, key file and key."""
    key_path = tmp_path / "aef.key"
    generate_receipt_signing_key(key_path)
    key = load_receipt_signing_key(key_path)
    db = tmp_path / "hub.db"
    with AefReceiptLog(db, hub_id="hub-a", signing_key=key) as log:
        _append(log, range(1, count + 1))
    return db, key_path, key


def _receipts(db: Path, key: ReceiptSigningKey) -> list[dict[str, object]]:
    with AefReceiptLog(db, hub_id="hub-a", signing_key=key) as log:
        return list(log.read_all())


def test_the_published_vectors_are_reproduced_byte_for_byte() -> None:
    lines: list[str] = []
    assert _tool().main(["--check"], out=lines.append) == 0
    assert lines[-1].endswith("is current")


@pytest.mark.parametrize("vector", _FIXTURE["vectors"], ids=lambda vector: vector["name"])
def test_every_published_vector_gets_its_verdict(vector: dict[str, Any]) -> None:
    trust = parse_aef_trust(json.dumps(_FIXTURE["trust"]).encode("utf-8"))
    report = verify_aef_sth(
        vector["receipts"], vector.get("sth", _FIXTURE["sth"]), trust_store=trust
    )
    assert report.verdict.value == vector["expected"], report.detail


def test_cli_issues_a_head_and_tells_complete_growth_truncation_and_two_sizes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key_path, key = _hub(tmp_path, count=3)
    trust = tmp_path / "trust.json"
    assert (
        _run(
            capsys,
            "aef",
            "trust",
            "--public-key",
            f"{key_path}.pub",
            "--hub-id",
            "hub-a",
            "--out",
            str(trust),
        )[0]
        == 0
    )
    old_head = tmp_path / "sth3.json"
    code, _out, err = _run(
        capsys,
        "aef",
        "sth",
        str(db),
        "--hub-id",
        "hub-a",
        "--signing-key",
        str(key_path),
        "--out",
        str(old_head),
    )
    assert code == 0 and "over 3 receipts" in err
    assert json.loads(old_head.read_text("utf-8"))["tree_size"] == 3

    with AefReceiptLog(db, hub_id="hub-a", signing_key=key) as log:
        _append(log, range(4, 6))
    new_head = tmp_path / "sth5.json"
    assert (
        _run(
            capsys,
            "aef",
            "sth",
            str(db),
            "--hub-id",
            "hub-a",
            "--signing-key",
            str(key_path),
            "--out",
            str(new_head),
        )[0]
        == 0
    )
    export = tmp_path / "all.jsonl"
    assert _run(capsys, "aef", "export", str(db), "--out", str(export))[0] == 0
    base = ["aef", "verify", "--trust", str(trust), "--now-ms", str(_NOW_MS), "--require-sth"]
    heads = ["--sth", str(old_head), "--sth", str(new_head)]

    code, out, _err = _run(capsys, *base, str(export), *heads, "--json")
    assert code == 0
    assert [(item["tree_size"], item["verdict"]) for item in json.loads(out)["sths"]] == [
        (3, "STH_COMPLETE"),
        (5, "STH_COMPLETE"),
    ]

    cut = tmp_path / "cut.jsonl"
    cut.write_text("".join(export.read_text("utf-8").splitlines(keepends=True)[:4]), "utf-8")
    code, out, _err = _run(capsys, *base, str(cut), *heads)
    assert code == 1
    assert "size=3: STH_COMPLETE" in out
    assert "size=5: STH_TRUNCATED (1 of 5 signed positions missing, first at seq 5)" in out
    # Without --require-sth the tree heads report but do not change the exit code.
    code, out, _err = _run(capsys, *base[:-1], str(cut), *heads)
    assert code == 0 and "STH_TRUNCATED" in out


def test_a_forged_receipt_past_the_head_and_a_bad_seq_neither_crash_nor_count(
    tmp_path: Path,
) -> None:
    db, _key_path, key = _hub(tmp_path, count=3)
    receipts = _receipts(db, key)
    head = sign_aef_sth(receipts[:2], hub_id="hub-a", signing_key=key)
    trust = parse_aef_trust(
        json.dumps(
            {
                "format": "aef-trust-v0.1",
                "keys": {
                    key.key_id: {
                        "public_key_hex": key.private_key.public_key().public_bytes_raw().hex(),
                        "revoked": False,
                    }
                },
                "logs": {head["log_id"]: key.key_id},
            }
        ).encode("utf-8")
    )
    forged = copy.deepcopy(receipts[2])
    forged["subject"] = {"task_id": "forged"}
    assert (
        verify_aef_sth([*receipts, forged], head, trust_store=trust).verdict
        is AefSthVerdict.STH_COMPLETE
    )
    for bad in ("1", 0, True, None):
        mangled = copy.deepcopy(receipts[:2])
        mangled[0]["seq"] = bad
        report = verify_aef_sth(mangled, head, trust_store=trust)
        assert report.verdict is AefSthVerdict.STH_TRUNCATED, bad
    unrepresentable = copy.deepcopy(receipts[:2])
    unrepresentable[1]["evidence"] = {"ratio": 0.5}
    assert (
        verify_aef_sth(unrepresentable, head, trust_store=trust).verdict
        is AefSthVerdict.STH_TRUNCATED
    )


def test_signing_refuses_a_log_it_cannot_vouch_for(tmp_path: Path) -> None:
    db, _key_path, key = _hub(tmp_path, count=3)
    receipts = _receipts(db, key)

    with pytest.raises(AefEmissionError, match="at least one receipt"):
        sign_aef_sth([], hub_id="hub-a", signing_key=key)
    with pytest.raises(AefEmissionError, match="is not seq 2 of log"):
        sign_aef_sth([receipts[0], receipts[2]], hub_id="hub-a", signing_key=key)
    with pytest.raises(AefEmissionError, match="is not seq 1 of log"):
        sign_aef_sth(receipts, hub_id="hub-b", signing_key=key)
    with pytest.raises(AefEmissionError, match="does not match its public key"):
        sign_aef_sth(
            receipts, hub_id="hub-a", signing_key=ReceiptSigningKey("0" * 16, key.private_key)
        )
    timeless = copy.deepcopy(receipts)
    timeless[1]["issued_at"] = "soon"
    with pytest.raises(AefEmissionError, match="seq 2 has no integer issued_at"):
        sign_aef_sth(timeless, hub_id="hub-a", signing_key=key)
    forged = copy.deepcopy(receipts)
    subject = forged[2]["subject"]
    assert isinstance(subject, dict)
    subject["task_id"] = "forged"
    with pytest.raises(AefEmissionError, match="receipt seq 3 is INVALID_RECEIPT_ID"):
        sign_aef_sth(forged, hub_id="hub-a", signing_key=key)
    # Two stores of the same log key and hub: valid receipts, but seq 2 links to
    # the other store's seq 1, so the chain is broken.
    second = tmp_path / "second.db"
    with AefReceiptLog(second, hub_id="hub-a", signing_key=key) as log:
        _append(log, range(7, 9))
    spliced = [receipts[0], _receipts(second, key)[1]]
    with pytest.raises(AefEmissionError, match="chain is broken"):
        sign_aef_sth(spliced, hub_id="hub-a", signing_key=key)


def test_cli_sth_refuses_bad_inputs_and_never_replaces_a_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key_path, _key = _hub(tmp_path, count=1)
    out = tmp_path / "sth.json"
    out.write_text("{}", "utf-8")
    sth = ["aef", "sth", str(db), "--hub-id", "hub-a", "--signing-key", str(key_path)]

    code, _o, err = _run(capsys, *sth, "--out", str(out))
    assert code == 2 and "cannot write" in err and out.read_text("utf-8") == "{}"
    code, printed, _e = _run(capsys, *sth)
    assert code == 0 and json.loads(printed)["tree_size"] == 1
    code, _o, err = _run(
        capsys,
        "aef",
        "sth",
        str(tmp_path / "none.db"),
        "--hub-id",
        "hub-a",
        "--signing-key",
        str(key_path),
    )
    assert code == 2 and "no hub store" in err
    code, _o, err = _run(capsys, *sth[:-2], "--signing-key", str(tmp_path / "missing.key"))
    assert code == 2 and err.startswith("error: ")
    code, _o, err = _run(capsys, *sth[:3], "--hub-id", "hub-z", "--signing-key", str(key_path))
    assert code == 2 and "is not seq 1 of log" in err


def test_cli_verify_refuses_an_unreadable_tree_head(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key_path, _key = _hub(tmp_path, count=1)
    trust = tmp_path / "trust.json"
    assert (
        _run(
            capsys,
            "aef",
            "trust",
            "--public-key",
            f"{key_path}.pub",
            "--hub-id",
            "hub-a",
            "--out",
            str(trust),
        )[0]
        == 0
    )
    export = tmp_path / "x.jsonl"
    assert _run(capsys, "aef", "export", str(db), "--out", str(export))[0] == 0
    bad = tmp_path / "bad.json"
    bad.write_text("[]", "utf-8")

    code, _o, err = _run(
        capsys, "aef", "verify", str(export), "--trust", str(trust), "--sth", str(bad)
    )
    assert code == 2 and "must be a JSON object" in err


def test_a_tree_head_with_a_malformed_log_id_is_invalid(tmp_path: Path) -> None:
    trust = parse_aef_trust(json.dumps(_FIXTURE["trust"]).encode("utf-8"))
    head = copy.deepcopy(_FIXTURE["sth"])
    head["log_id"] = "not-a-log-id"

    report = verify_aef_sth([], head, trust_store=trust)

    assert (report.verdict, report.log_id) == (AefSthVerdict.STH_INVALID, None)
