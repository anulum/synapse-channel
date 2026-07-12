# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — signed capability-card lifecycle CLI tests
"""End-to-end local CLI tests for key generation, signing, and verification."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from synapse_channel import cli, cli_capability_cards
from synapse_channel.core.capability_card_trust import CapabilityCardTrustError


def _card(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "agent": "P/worker",
                "project": "P",
                "description": "worker",
                "skills": ["python"],
                "task_classes": ["code"],
                "contracts": [],
                "meta": {},
                "manifest_digest": "sha256:abc",
            }
        ),
        encoding="utf-8",
    )
    return path


def _keygen(tmp_path: Path) -> tuple[Path, Path]:
    key = tmp_path / "card.pem"
    trust = tmp_path / "trust.json"
    assert (
        cli.main(
            [
                "capability-card",
                "keygen",
                "--key-id",
                "P:key",
                "--private-out",
                str(key),
                "--agent",
                "P/worker",
                "--project",
                "P",
                "--trust",
                str(trust),
            ]
        )
        == 0
    )
    return key, trust


def test_cli_keygen_sign_and_verify_round_trip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key, trust = _keygen(tmp_path)
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert stat.S_IMODE(trust.stat().st_mode) == 0o600
    capsys.readouterr()

    card = _card(tmp_path / "card.json")
    signed = tmp_path / "signed.json"
    assert (
        cli.main(
            [
                "capability-card",
                "sign",
                str(card),
                "--key",
                str(key),
                "--key-id",
                "P:key",
                "--sequence",
                "1",
                "--signed-at",
                "100",
                "--expires-at",
                "200",
                "--out",
                str(signed),
            ]
        )
        == 0
    )
    assert stat.S_IMODE(signed.stat().st_mode) == 0o600
    capsys.readouterr()

    assert (
        cli.main(
            [
                "capability-card",
                "verify",
                str(signed),
                "--trust",
                str(trust),
                "--now",
                "150",
                "--manifest-digest",
                "sha256:abc",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == "valid"
    assert payload["key_id"] == "P:key"
    assert payload["sequence"] == 1
    assert (
        cli.main(
            [
                "capability-card",
                "verify",
                str(signed),
                "--trust",
                str(trust),
                "--now",
                "150",
            ]
        )
        == 0
    )
    plain = capsys.readouterr().out
    assert "valid:" in plain
    assert "key=P:key sequence=1" in plain


def test_cli_sign_can_override_bindings_and_print_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key, _trust = _keygen(tmp_path)
    capsys.readouterr()
    card = tmp_path / "card.json"
    card.write_text('{"description":"x"}', encoding="utf-8")
    assert (
        cli.main(
            [
                "capability-card",
                "sign",
                str(card),
                "--key",
                str(key),
                "--key-id",
                "P:key",
                "--sequence",
                "1",
                "--agent",
                "P/worker",
                "--project",
                "P",
                "--manifest-digest",
                "sha256:abc",
                "--signed-at",
                "100",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["agent"] == "P/worker"
    assert output["project"] == "P"
    assert output["manifest_digest"] == "sha256:abc"


def test_cli_keygen_without_trust_prints_public_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = tmp_path / "card.pem"
    assert (
        cli.main(
            [
                "capability-card",
                "keygen",
                "--key-id",
                "P:key",
                "--private-out",
                str(key),
                "--agent",
                "P/worker",
                "--project",
                "P",
                "--expires-at",
                "200",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert '"key_id": "P:key"' in output
    assert '"expires_at": 200.0' in output
    assert "PRIVATE" not in output

    key_without_expiry = tmp_path / "card-no-expiry.pem"
    assert (
        cli.main(
            [
                "capability-card",
                "keygen",
                "--key-id",
                "P:key-2",
                "--private-out",
                str(key_without_expiry),
                "--agent",
                "P/worker",
                "--project",
                "P",
            ]
        )
        == 0
    )
    assert "expires_at" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("field_args", "match"),
    [
        (["--key-id", ""], "non-empty key_id"),
        (["--agent", ""], "non-empty agent bindings"),
        (["--project", ""], "non-empty project bindings"),
        (["--expires-at", "nan"], "expires_at must be finite"),
    ],
)
def test_cli_keygen_validates_public_fields_before_writing_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    field_args: list[str],
    match: str,
) -> None:
    key = tmp_path / (match.split()[0] + ".pem")
    args = [
        "capability-card",
        "keygen",
        "--key-id",
        "P:key",
        "--private-out",
        str(key),
        "--agent",
        "P/worker",
        "--project",
        "P",
        "--trust",
        str(tmp_path / "trust.json"),
    ]
    option = field_args[0]
    if option in args:
        option_index = args.index(option)
        args[option_index : option_index + 2] = field_args
    else:
        args.extend(field_args)

    assert cli.main(args) == 2
    assert match in capsys.readouterr().err
    assert not key.exists()


def test_cli_refuses_duplicate_key_and_existing_signed_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key, trust = _keygen(tmp_path)
    capsys.readouterr()
    duplicate = [
        "capability-card",
        "keygen",
        "--key-id",
        "P:key",
        "--private-out",
        str(tmp_path / "other.pem"),
        "--agent",
        "P/worker",
        "--project",
        "P",
        "--trust",
        str(trust),
    ]
    assert cli.main(duplicate) == 2
    assert "already enrolled" in capsys.readouterr().err
    assert not (tmp_path / "other.pem").exists()

    card = _card(tmp_path / "card.json")
    output = tmp_path / "signed.json"
    output.write_text("occupied", encoding="utf-8")
    assert (
        cli.main(
            [
                "capability-card",
                "sign",
                str(card),
                "--key",
                str(key),
                "--key-id",
                "P:key",
                "--sequence",
                "1",
                "--out",
                str(output),
            ]
        )
        == 2
    )
    assert "File exists" in capsys.readouterr().err


def test_cli_keygen_reports_when_failed_enrolment_key_cannot_be_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "stranded.pem"

    def fail_enrolment(*_args: object, **_kwargs: object) -> None:
        raise CapabilityCardTrustError("trust refused")

    def fail_unlink(_self: Path, *, missing_ok: bool = False) -> None:
        del missing_ok
        raise OSError("read-only filesystem")

    monkeypatch.setattr(
        "synapse_channel.cli_capability_cards.enroll_capability_card_key", fail_enrolment
    )
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    assert (
        cli.main(
            [
                "capability-card",
                "keygen",
                "--key-id",
                "P:key",
                "--private-out",
                str(key),
                "--agent",
                "P/worker",
                "--project",
                "P",
                "--trust",
                str(tmp_path / "trust.json"),
            ]
        )
        == 2
    )
    error = capsys.readouterr().err
    assert "trust refused" in error
    assert "generated key could not be removed: read-only filesystem" in error
    assert key.exists()


def test_cli_verify_distinguishes_invalid_card_from_bad_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _key, trust = _keygen(tmp_path)
    capsys.readouterr()
    unsigned = _card(tmp_path / "unsigned.json")
    assert cli.main(["capability-card", "verify", str(unsigned), "--trust", str(trust)]) == 1
    assert "missing_signature" in capsys.readouterr().out

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    assert cli.main(["capability-card", "verify", str(malformed), "--trust", str(trust)]) == 2
    assert "verify error" in capsys.readouterr().err


def test_cli_verify_requires_bindings_when_card_omits_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _key, trust = _keygen(tmp_path)
    capsys.readouterr()
    card = tmp_path / "bare.json"
    card.write_text("{}", encoding="utf-8")
    assert cli.main(["capability-card", "verify", str(card), "--trust", str(trust)]) == 2
    assert "requires agent and project" in capsys.readouterr().err


def test_write_new_removes_partial_output_on_flush_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "partial.json"

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("disk failed")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="disk failed"):
        cli_capability_cards._write_new(output, "{}\n")
    assert not output.exists()
