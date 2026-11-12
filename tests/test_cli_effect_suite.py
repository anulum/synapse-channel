# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse effect-suite run` and `verify` through the real entry point
"""The command prints failures and unknowns first, signs every record and verifies them offline."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from synapse_channel.cli import main
from synapse_channel.core.receipt_signing import generate_receipt_signing_key
from synapse_channel.effect_suite.record import NEEDS_CRYPTOGRAPHY, load_signing_key
from synapse_channel.effect_suite.variants import DRIVERS, Stage
from synapse_channel.effect_suite.workspace import git_out


@pytest.fixture
def key(tmp_path: Path) -> Path:
    path = tmp_path / "keys" / "suite.key"
    path.parent.mkdir()
    generate_receipt_signing_key(path)
    return path


def _run(key: Path, output: Path, *variants: str) -> list[str]:
    argv = ["effect-suite", "run", "--signing-key", str(key), "--output", str(output)]
    for name in variants:
        argv += ["--variant", name]
    return argv


def test_an_unknown_variant_missing_git_or_an_unusable_key_is_refused_before_running(
    key: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "records"
    assert main(_run(key, output, "9z", "2a", "0")) == 2
    assert capsys.readouterr().out == "effect suite refused: no variant is called 0, 9z\n"
    key.chmod(0o644)
    assert main(_run(key, output)) == 2
    assert capsys.readouterr().out == (
        f"effect suite refused: cannot use the signing key {key}: SecurePathError\n"
    )
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert main(_run(key, output)) == 2
    assert capsys.readouterr().out == (
        "effect suite refused: this needs a git executable on PATH, and none was found\n"
    )
    assert not output.exists()


def test_without_the_cryptography_package_both_commands_refuse_with_the_extra_to_install(
    key: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "cryptography", None)
    output = tmp_path / "records"
    assert main(_run(key, output)) == 2
    assert capsys.readouterr().out == f"effect suite refused: {NEEDS_CRYPTOGRAPHY}\n"
    assert main(["effect-suite", "verify", "--public-key", f"{key}.pub", str(key)]) == 2
    assert capsys.readouterr().out == f"effect suite verify refused: {NEEDS_CRYPTOGRAPHY}\n"
    assert not output.exists()


def test_a_passing_run_exits_zero_and_its_records_verify(
    key: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "records"
    assert main(_run(key, output, "3f", "2d")) == 0
    key_id = load_signing_key(key).key_id
    assert capsys.readouterr().out.splitlines() == [
        "pass 2d: every reading matches (effect-suite-2d.json)",
        "not_applicable 3f: intents of this version carry no revision (effect-suite-3f.json)",
        f"2 records written to {output}, signed by key {key_id}",
    ]
    records = sorted(output.glob("effect-suite-*.json"))
    argv = ["effect-suite", "verify", "--public-key", f"{key}.pub", *map(str, records)]
    assert main(argv) == 0
    assert capsys.readouterr().out.splitlines() == [f"valid {path}" for path in records]


def test_a_failed_or_unknown_variant_prints_first_and_exits_one(
    key: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def unreadable(stage: Stage) -> None:
        path, git = stage.repository.path, stage.repository.git
        git_out(path, ["rev-parse", "--verify", "--quiet", "refs/heads/absent"], git=git)

    monkeypatch.setitem(DRIVERS, "2d", DRIVERS["2a"])
    monkeypatch.setitem(DRIVERS, "1", unreadable)
    key_id = load_signing_key(key).key_id
    unknown = "unknown 1: a reading could not be made: git rev-parse failed: exit code 1"
    alone = tmp_path / "alone"
    assert main(_run(key, alone, "1")) == 1
    assert capsys.readouterr().out.splitlines() == [
        f"{unknown} (effect-suite-1.json)",
        f"1 records written to {alone}, signed by key {key_id}",
    ]
    output = tmp_path / "records"
    assert main(_run(key, output, "3f", "2a", "2d", "1")) == 1
    assert capsys.readouterr().out.splitlines() == [
        "fail 2d: answers ('declare:accepted', 'not_approved'), expected ('declare:accepted',)"
        " (effect-suite-2d.json)",
        f"{unknown} (effect-suite-1.json)",
        "pass 2a: every reading matches (effect-suite-2a.json)",
        "not_applicable 3f: intents of this version carry no revision (effect-suite-3f.json)",
        f"4 records written to {output}, signed by key {key_id}",
    ]


def test_verify_says_which_record_does_not_verify_and_refuses_what_it_cannot_read(
    key: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "records"
    assert main(_run(key, output, "3f")) == 0
    capsys.readouterr()
    record = output / "effect-suite-3f.json"
    changed = tmp_path / "changed.json"
    changed.write_text(
        json.dumps({**json.loads(record.read_text(encoding="utf-8")), "verdict": "pass"}),
        encoding="utf-8",
    )
    listed = tmp_path / "listed.json"
    listed.write_text("[1, 2]", encoding="utf-8")
    verify = ["effect-suite", "verify", "--public-key", f"{key}.pub"]
    assert main([*verify, str(record), str(changed), str(listed)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        f"valid {record}",
        f"invalid {changed}: the signature does not match the record",
        f"invalid {listed}: not a JSON object",
    ]
    missing = tmp_path / "missing.json"
    assert main([*verify, str(missing)]) == 2
    assert capsys.readouterr().out == (
        f"effect suite verify refused: cannot read {missing}: FileNotFoundError\n"
    )
    assert main(["effect-suite", "verify", "--public-key", str(missing), str(record)]) == 2
    assert capsys.readouterr().out == (
        f"effect suite verify refused: cannot read receipt verification key {missing}: "
        f"receipt-verification-key: cannot securely open {missing}: No such file or directory\n"
    )
