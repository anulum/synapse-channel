# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the AEF chain vectors through the library and the command
"""Check AEF chain continuity against the published chain vectors."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from synapse_channel import cli
from synapse_channel.core.aef_chain import (
    AUTHENTIC_VERDICTS,
    GENESIS_RECEIPT,
    AefChainCode,
    verify_aef_chain,
)
from synapse_channel.core.aef_emission import _GENESIS_RECEIPT
from synapse_channel.core.aef_trust_file import AEF_TRUST_FORMAT
from synapse_channel.core.aef_verdict import AefVerdictCode
from synapse_channel.core.aef_verification import (
    AefReceiptIndex,
    AefTrustedKey,
    AefTrustStore,
    verify_aef_receipt,
)

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = json.loads((_ROOT / "tests" / "fixtures" / "aef_chain_v0_1.json").read_text("utf-8"))
_VECTORS = {vector["name"]: vector for vector in _FIXTURE["vectors"]}


def _store() -> AefTrustStore:
    trust = _FIXTURE["trust_store"]
    keys = {
        key_id: AefTrustedKey(bytes.fromhex(entry["public_key_hex"]), revoked=entry["revoked"])
        for key_id, entry in trust["keys"].items()
    }
    return AefTrustStore(keys=keys, logs=dict(trust["logs"]))


def _check(vector: dict[str, Any]) -> tuple[list[str], Any]:
    index = AefReceiptIndex()
    checked = [
        (
            receipt,
            verify_aef_receipt(receipt, trust_store=_store(), now_ms=vector["now_ms"], seen=index),
        )
        for receipt in vector["receipts"]
    ]
    (report,) = verify_aef_chain(checked)
    return [outcome.verdict.value for _receipt, outcome in checked], report


@pytest.mark.parametrize("name", sorted(_VECTORS))
def test_every_chain_vector_gets_its_verdicts(name: str) -> None:
    vector = _VECTORS[name]
    verdicts, report = _check(vector)
    assert verdicts == vector["expected_receipt_verdicts"]
    assert report.verdict.value == vector["expected_chain"]["verdict"]
    assert [[issue.code.value, issue.seq] for issue in report.issues] == vector["expected_chain"][
        "issues"
    ]
    assert report.continuous is (report.verdict is AefChainCode.CHAIN_CONTINUOUS)


def test_the_vectors_cover_every_finding_and_both_authentic_verdicts() -> None:
    codes = {
        issue[0] for vector in _FIXTURE["vectors"] for issue in vector["expected_chain"]["issues"]
    } | {vector["expected_chain"]["verdict"] for vector in _FIXTURE["vectors"]}
    assert codes == {code.value for code in AefChainCode}
    assert AUTHENTIC_VERDICTS == {AefVerdictCode.VALID, AefVerdictCode.EXPIRED}
    assert GENESIS_RECEIPT == _GENESIS_RECEIPT == _FIXTURE["genesis_receipt"]


def test_a_log_without_an_authentic_receipt_has_no_report() -> None:
    vector = _VECTORS["c09-hole-from-invalid"]
    forged = vector["receipts"][1]
    outcome = verify_aef_receipt(forged, trust_store=_store(), now_ms=vector["now_ms"])
    assert verify_aef_chain([(forged, outcome)]) == []
    assert verify_aef_chain([]) == []


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "aef_chain_vectors", _ROOT / "tools" / "aef_chain_vectors.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["aef_chain_vectors"] = module
    spec.loader.exec_module(module)
    return module


def test_the_published_vectors_are_reproduced_byte_for_byte() -> None:
    lines: list[str] = []
    assert _tool().main(["--check"], out=lines.append) == 0
    assert lines[-1].endswith("is current")


def _trust_file(tmp_path: Path) -> Path:
    trust = _FIXTURE["trust_store"]
    path = tmp_path / "trust.json"
    path.write_text(json.dumps({"format": AEF_TRUST_FORMAT, **trust}), encoding="utf-8")
    return path


def _verify(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str, *extra: str
) -> tuple[int, str]:
    receipts = tmp_path / f"{name}.json"
    receipts.write_text(json.dumps(_VECTORS[name]["receipts"]), encoding="utf-8")
    code = cli.main(
        [
            "aef",
            "verify",
            str(receipts),
            "--trust",
            str(_trust_file(tmp_path)),
            "--now-ms",
            str(_VECTORS[name]["now_ms"]),
            *extra,
        ]
    )
    return code, capsys.readouterr().out


def test_the_command_reports_each_chain_and_gates_on_it_only_when_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _verify(tmp_path, capsys, "c04-missing-prev")
    assert code == 0
    assert (
        "chain log=f3320c94a8b070b0 seq 1..3: CHAIN_BROKEN_LINK (CHAIN_BROKEN_LINK at seq 3)" in out
    )
    code, out = _verify(tmp_path, capsys, "c04-missing-prev", "--require-chain")
    assert code == 1
    code, out = _verify(tmp_path, capsys, "c01-continuous", "--require-chain")
    assert (code, "seq 1..3: CHAIN_CONTINUOUS\n" in out) == (0, True)
    code, out = _verify(tmp_path, capsys, "c07-open-start", "--require-chain", "--json")
    (chain,) = json.loads(out)["chains"]
    assert code == 0
    assert chain["verdict"] == "CHAIN_CONTINUOUS"
    assert chain["issues"][0]["code"] == "CHAIN_OPEN_START"
    assert (chain["first_seq"], chain["last_seq"], chain["receipts"]) == (5, 6, 2)


def test_far_apart_receipts_cost_their_count_not_their_seq_range() -> None:
    import time

    from synapse_channel.core.aef_chain import AefChainIssue
    from synapse_channel.core.aef_verification import AefVerification

    top = (1 << 53) - 1
    log_id = next(iter(_FIXTURE["trust_store"]["logs"]))
    entries = [
        (
            {"log_id": log_id, "seq": seq, "prev_receipt": prev},
            AefVerification(AefVerdictCode.VALID, receipt_id=f"aef1:{seq:064x}"),
        )
        for seq, prev in ((1, GENESIS_RECEIPT), (top, "aef1:" + "f" * 64))
    ]
    started = time.monotonic()
    (report,) = verify_aef_chain(entries)
    assert time.monotonic() - started < 1.0
    assert report.issues == (AefChainIssue(AefChainCode.CHAIN_GAP, 2, f"seq 2..{top - 1} missing"),)
