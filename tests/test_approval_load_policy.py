# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the approval-load policy file: validation, owner-only loading, hub start
"""The approval-load policy is validated exactly, read from an owner-only file, fail-closed."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from synapse_channel.cli import build_parser
from synapse_channel.cli_hub_auth import load_approval_load_policy_for_hub
from synapse_channel.cli_hub_startup import HubStartupRefused
from synapse_channel.core.approval_load import ApprovalLoad
from synapse_channel.core.approval_load_policy import (
    APPROVAL_LOAD_POLICY_SCHEMA,
    MAX_POLICY_BYTES,
    ApprovalLoadPolicyError,
    ApprovalLoadRule,
    load_approval_load_policy,
    parse_approval_load_policy,
)
from synapse_channel.core.hub_config import HubConfig

RULE = {"request_rate": 3, "window_seconds": 60, "min_review_seconds": 5, "max_batch": 2}


def _document(**sections: object) -> dict[str, object]:
    return {"schema": APPROVAL_LOAD_POLICY_SCHEMA, **sections}


def _write(path: Path, data: object, *, mode: int = 0o600) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(mode)
    return path


def test_a_valid_policy_parses_both_sections_and_has_a_stable_digest() -> None:
    policy = parse_approval_load_policy(
        _document(roles={"P/person": RULE}, approvers={"Q/alice": {**RULE, "max_batch": 9}})
    )
    assert policy.roles["P/person"] == ApprovalLoadRule(3, 60.0, 5.0, 2)
    assert policy.approvers["Q/alice"].max_batch == 9
    assert policy.projects == frozenset({"P", "Q"})
    assert policy.longest_window_seconds == 60.0
    reordered = parse_approval_load_policy(
        {
            "approvers": {"Q/alice": {**RULE, "max_batch": 9}},
            "roles": {"P/person": dict(reversed(list(RULE.items())))},
            "schema": APPROVAL_LOAD_POLICY_SCHEMA,
        }
    )
    assert reordered.sha256 == policy.sha256
    assert len(policy.sha256) == 64
    assert policy.to_json_obj()["schema"] == APPROVAL_LOAD_POLICY_SCHEMA
    changed = parse_approval_load_policy(_document(roles={"P/person": {**RULE, "request_rate": 4}}))
    assert changed.sha256 != policy.sha256


def test_an_absent_section_is_empty() -> None:
    policy = parse_approval_load_policy(_document(approvers={"P/alice": RULE}))
    assert policy.roles == {}
    assert policy.longest_window_seconds == 60.0


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "JSON object"),
        ({"roles": {"P/person": RULE}}, "schema must be"),
        (_document(roles={"P/person": RULE}, extra=1), "unknown keys"),
        (_document(), "declares no rule"),
        (_document(roles=[]), "roles must be a mapping"),
        (_document(roles={"person": RULE}), "<project>/<name>"),
        (_document(roles={"/person": RULE}), "<project>/<name>"),
        (_document(roles={"P/": RULE}), "<project>/<name>"),
        (_document(roles={" P/person": RULE}), "<project>/<name>"),
        (_document(roles={"P/person": 3}), "mapping of the four thresholds"),
        (_document(roles={"P/person": {"request_rate": 1}}), "must declare exactly"),
        (_document(roles={"P/person": {**RULE, "extra": 1}}), "must declare exactly"),
        (_document(roles={"P/person": {**RULE, "request_rate": 0}}), "request_rate"),
        (_document(roles={"P/person": {**RULE, "request_rate": True}}), "request_rate"),
        (_document(roles={"P/person": {**RULE, "request_rate": 1.5}}), "request_rate"),
        (_document(roles={"P/person": {**RULE, "max_batch": 0}}), "max_batch"),
        (_document(roles={"P/person": {**RULE, "window_seconds": 0}}), "positive"),
        (_document(roles={"P/person": {**RULE, "window_seconds": "60"}}), "number of seconds"),
        (_document(roles={"P/person": {**RULE, "window_seconds": False}}), "number of seconds"),
        (_document(roles={"P/person": {**RULE, "min_review_seconds": -1}}), "non-negative"),
    ],
)
def test_a_malformed_policy_is_refused(data: object, message: str) -> None:
    with pytest.raises(ApprovalLoadPolicyError, match=message):
        parse_approval_load_policy(data)


def test_non_finite_seconds_are_refused() -> None:
    with pytest.raises(ApprovalLoadPolicyError, match="finite"):
        parse_approval_load_policy(
            _document(roles={"P/person": {**RULE, "min_review_seconds": float("inf")}})
        )


def test_a_zero_minimum_review_time_is_allowed() -> None:
    policy = parse_approval_load_policy(
        _document(roles={"P/person": {**RULE, "min_review_seconds": 0}})
    )
    assert policy.roles["P/person"].min_review_seconds == 0.0


def test_an_owner_only_file_loads(tmp_path: Path) -> None:
    path = _write(tmp_path / "policy.json", _document(roles={"P/person": RULE}))
    assert load_approval_load_policy(path).roles["P/person"].request_rate == 3


def test_a_file_other_users_can_read_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path / "policy.json", _document(roles={"P/person": RULE}), mode=0o640)
    if os.name == "nt":
        # Windows decides access from the file's access list, not from its mode bits, so
        # the local users group is granted read there.
        subprocess.run(["icacls", str(path), "/grant", "*S-1-5-32-545:(R)"], check=True)
    # POSIX names the mode, Windows the foreign access entry; both refuse the file.
    with pytest.raises(ApprovalLoadPolicyError, match="is accessible by other (users|principals)"):
        load_approval_load_policy(path)


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ApprovalLoadPolicyError, match="approval-load policy"):
        load_approval_load_policy(tmp_path / "absent.json")


def test_a_symlinked_file_is_refused(tmp_path: Path) -> None:
    target = _write(tmp_path / "policy.json", _document(roles={"P/person": RULE}))
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(ApprovalLoadPolicyError, match="symlink"):
        load_approval_load_policy(link)


def test_a_hard_linked_file_is_refused(tmp_path: Path) -> None:
    target = _write(tmp_path / "policy.json", _document(roles={"P/person": RULE}))
    os.link(target, tmp_path / "second.json")
    with pytest.raises(ApprovalLoadPolicyError, match="hard links"):
        load_approval_load_policy(target)


def test_an_oversized_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(" " * (MAX_POLICY_BYTES + 1), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(ApprovalLoadPolicyError, match="exceeds"):
        load_approval_load_policy(path)


@pytest.mark.parametrize("raw", [b"{", b"\xff\xfe"])
def test_a_file_that_is_not_utf8_json_is_refused(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "policy.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    with pytest.raises(ApprovalLoadPolicyError, match="not UTF-8 JSON"):
        load_approval_load_policy(path)


def test_a_file_replaced_while_opening_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "policy.json", _document(roles={"P/person": RULE}))
    other = _write(tmp_path / "other.json", _document(roles={"P/person": RULE}))
    real_open = os.open

    def swapping_open(file: Any, flags: int, *args: Any) -> int:
        return real_open(other, flags, *args)

    monkeypatch.setattr(os, "open", swapping_open)
    with pytest.raises(ApprovalLoadPolicyError, match="changed while it was opened"):
        load_approval_load_policy(path)


def _startup(path: str, db: str) -> Any:
    """Return the two attributes the hub startup stage reads."""
    return SimpleNamespace(
        args=argparse.Namespace(approval_load_policy=path, db=db), config=HubConfig()
    )


def test_hub_startup_loads_the_policy_or_refuses_to_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unset = _startup("", str(tmp_path / "hub.db"))
    load_approval_load_policy_for_hub(unset)
    assert unset.config.auth.approval_load is None

    good = _write(tmp_path / "policy.json", _document(roles={"P/person": RULE}))
    loaded = _startup(str(good), str(tmp_path / "hub.db"))
    load_approval_load_policy_for_hub(loaded)
    assert isinstance(loaded.config.auth.approval_load, ApprovalLoad)
    assert loaded.config.auth.approval_load.policy.roles["P/person"].max_batch == 2

    with pytest.raises(HubStartupRefused):
        load_approval_load_policy_for_hub(_startup(str(good), ""))
    assert "requires --db" in capsys.readouterr().err

    bad = _write(tmp_path / "bad.json", _document())
    with pytest.raises(HubStartupRefused):
        load_approval_load_policy_for_hub(_startup(str(bad), str(tmp_path / "hub.db")))
    assert "declares no rule" in capsys.readouterr().err

    args = build_parser().parse_args(["hub", "--approval-load-policy", str(good)])
    assert args.approval_load_policy == str(good)
    assert build_parser().parse_args(["hub"]).approval_load_policy == ""
