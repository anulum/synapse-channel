# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the custodian map decides who answers for an orphaned mailbox
"""One order decides the custodian, and a bad map is refused rather than guessed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from synapse_channel.cli import build_parser
from synapse_channel.cli_hub_auth import load_mailbox_custodians_for_hub
from synapse_channel.cli_hub_startup import HubStartupRefused
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.mailbox_custodians import (
    MailboxCustodianError,
    MailboxCustodians,
    load_mailbox_custodians,
    parse_mailbox_custodians,
)


def test_the_name_entry_wins_then_the_project_then_the_wildcard() -> None:
    custodians = parse_mailbox_custodians(
        {"*": "OWNER/owner", "P": "P/manager", "P/seat": "P/lead", "BARE": "OWNER/desk"}
    )
    assert custodians.custodian_for("P/seat") == "P/lead"
    assert custodians.custodian_for(" P/other ") == "P/manager"
    assert custodians.custodian_for("Q/seat") == "OWNER/owner"
    assert custodians.custodian_for("BARE") == "OWNER/desk"
    assert custodians.custodian_for("lonely") == "OWNER/owner"
    assert custodians.custodian_for("") is None


def test_without_a_matching_entry_there_is_no_custodian() -> None:
    custodians = parse_mailbox_custodians({"P": "P/manager"})
    assert custodians.custodian_for("Q/seat") is None
    assert custodians.custodian_for("P") == "P/manager"
    assert MailboxCustodians().custodian_for("P/seat") is None


def test_a_missing_file_is_an_empty_map(tmp_path: Path) -> None:
    custodians = load_mailbox_custodians(tmp_path / "absent.json")
    assert custodians.entries == {}
    assert custodians.custodian_for("P/seat") is None


def test_a_file_is_read_with_home_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "custodians.json").write_text(json.dumps({"*": "OWNER/owner"}), encoding="utf-8")
    assert load_mailbox_custodians("~/custodians.json").custodian_for("P/x") == "OWNER/owner"


@pytest.mark.parametrize("text", ["{", "[]", '"P"', b"\xff\xfe".decode("latin-1")])
def test_a_file_that_is_not_a_json_object_is_refused(tmp_path: Path, text: str) -> None:
    path = tmp_path / "custodians.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(MailboxCustodianError):
        load_mailbox_custodians(path)


def test_a_file_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "custodians.json"
    path.write_bytes(b'{"*": "\xff"}')
    with pytest.raises(MailboxCustodianError, match="not valid JSON"):
        load_mailbox_custodians(path)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"P": ""}, "non-empty"),
        ({"P": 7}, "non-empty"),
        ({"": "P/manager"}, "non-empty"),
        ({"P": "seat@OTHER-HUB"}, "another hub"),
        ({"P/*": "P/manager"}, "pattern"),
        ({"P": "P/?"}, "pattern"),
        ({"/seat": "P/manager"}, "<project>/<name>"),
        ({"P": "P/ "}, "<project>/<name>"),
        ({"*": "A/a", " * ": "B/b"}, "twice"),
        ({"P": "A/a", " P": "B/b"}, "twice"),
    ],
)
def test_a_malformed_entry_is_refused(data: dict[str, object], message: str) -> None:
    with pytest.raises(MailboxCustodianError, match=message):
        parse_mailbox_custodians(data)


def _startup(path: str) -> Any:
    """Return the two attributes the hub startup stage reads."""
    return SimpleNamespace(args=argparse.Namespace(mailbox_custodians=path), config=HubConfig())


def test_hub_startup_loads_the_map_and_refuses_a_malformed_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unset = _startup("")
    load_mailbox_custodians_for_hub(unset)
    assert unset.config.auth.mailbox_custodians is None
    good = tmp_path / "custodians.json"
    good.write_text(json.dumps({"P": "P/manager"}), encoding="utf-8")
    loaded = _startup(str(good))
    load_mailbox_custodians_for_hub(loaded)
    assert loaded.config.auth.mailbox_custodians == {"P": "P/manager"}
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(HubStartupRefused):
        load_mailbox_custodians_for_hub(_startup(str(bad)))
    assert "custodian map is not valid JSON" in capsys.readouterr().err
    parser = build_parser()
    args = parser.parse_args(["hub", "--mailbox-custodians", str(good)])
    assert args.mailbox_custodians == str(good)
