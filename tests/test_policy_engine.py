# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the advisory policy engine and rule families

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from synapse_channel.core.policy_engine import (
    FAIL,
    NOT_APPLICABLE,
    PASS,
    WARN,
    PolicyConfig,
    PolicyDecision,
    PolicyError,
    gate_blocks,
    load_policy,
    overall_status,
)
from synapse_channel.core.policy_rules import evaluate_policy
from synapse_channel.core.receipts import ReleaseReceipt


def _receipt(**overrides: Any) -> ReleaseReceipt:
    base: dict[str, Any] = {
        "task_id": "T1",
        "owner": "alice",
        "evidence": [],
        "artifacts": [],
        "known_failures": [],
        "changed_files": [],
        "generated_artifacts": [],
        "approvals": [],
        "epistemic_status": "supported",
        "epistemic_reasons": [],
    }
    base.update(overrides)
    return cast(ReleaseReceipt, base)


# --- configuration loading -------------------------------------------------


def test_load_json_policy(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"version": 1, "mode": "advisory", "rules": {"x": {}}}))
    config = load_policy(path)
    assert config.mode == "advisory"
    assert config.rules == {"x": {}}


def test_load_toml_policy(tmp_path: Path) -> None:
    path = tmp_path / "p.toml"
    path.write_text(
        'version = 1\nmode = "enforcement"\n[rules.required_tests]\ncommands = ["pytest"]\n'
    )
    config = load_policy(path)
    assert config.mode == "enforcement"
    assert config.rules["required_tests"]["commands"] == ["pytest"]


def test_load_policy_defaults_mode_and_version(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"rules": {}}))
    config = load_policy(path)
    assert config.version == 1
    assert config.mode == "advisory"


@pytest.mark.parametrize(
    ("content", "match"),
    [
        (json.dumps({"version": 2}), "unsupported policy version"),
        (json.dumps({"mode": "nonsense"}), "policy mode must be"),
        (json.dumps({"rules": []}), "'rules' must be a mapping"),
        (json.dumps([1, 2, 3]), "must contain a mapping"),
        ("{not valid json", "invalid JSON policy"),
    ],
)
def test_load_policy_rejects_bad_files(tmp_path: Path, content: str, match: str) -> None:
    path = tmp_path / "p.json"
    path.write_text(content)
    with pytest.raises(PolicyError, match=match):
        load_policy(path)


def test_load_policy_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PolicyError, match="does not exist"):
        load_policy(tmp_path / "absent.json")


def test_load_policy_unsupported_suffix(tmp_path: Path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text("version: 1")
    with pytest.raises(PolicyError, match="unsupported policy file type"):
        load_policy(path)


def test_load_policy_rejects_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "p.toml"
    path.write_text("this is = = not toml")
    with pytest.raises(PolicyError, match="invalid TOML policy"):
        load_policy(path)


def test_toml_policy_without_any_loader_raises_with_a_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_toml(name: str) -> Any:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("synapse_channel.core.policy_engine.importlib.import_module", _no_toml)
    path = tmp_path / "p.toml"
    path.write_text("version = 1\n")
    with pytest.raises(PolicyError, match="need Python 3.11"):
        load_policy(path)


def test_toml_loader_falls_back_to_tomli(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib
    import types

    from synapse_channel.core import policy_engine

    fake_tomli = types.SimpleNamespace(loads=lambda text: {"loaded_by": "tomli", "text": text})
    real_import = importlib.import_module

    def _only_tomli(name: str) -> Any:
        if name == "tomllib":
            raise ModuleNotFoundError(name)
        if name == "tomli":
            return fake_tomli
        return real_import(name)

    monkeypatch.setattr("synapse_channel.core.policy_engine.importlib.import_module", _only_tomli)
    loader = policy_engine._toml_loader()
    assert loader(b"x = 1")["loaded_by"] == "tomli"


# --- decision aggregation --------------------------------------------------


def test_decision_as_dict_is_json_serialisable() -> None:
    decision = PolicyDecision("r", WARN, "T1", "because", evidence=("e1",), next_action="do x")
    payload = decision.as_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["evidence"] == ["e1"]


def test_overall_status_picks_the_worst() -> None:
    assert overall_status([]) == PASS
    decisions = [
        PolicyDecision("a", PASS, "T", ""),
        PolicyDecision("b", WARN, "T", ""),
        PolicyDecision("c", NOT_APPLICABLE, "T", ""),
    ]
    assert overall_status(decisions) == WARN
    decisions.append(PolicyDecision("d", FAIL, "T", ""))
    assert overall_status(decisions) == FAIL


def test_gate_blocks_only_in_enforcement_on_fail() -> None:
    failing = [PolicyDecision("a", FAIL, "T", "")]
    passing = [PolicyDecision("a", PASS, "T", "")]
    assert gate_blocks(failing, PolicyConfig(1, "advisory", {})) is False
    assert gate_blocks(failing, PolicyConfig(1, "enforcement", {})) is True
    assert gate_blocks(passing, PolicyConfig(1, "enforcement", {})) is False


# --- rule families ---------------------------------------------------------


def _evaluate(rule: str, rule_cfg: dict[str, Any], **receipt: Any) -> PolicyDecision:
    config = PolicyConfig(1, "advisory", {rule: rule_cfg})
    return evaluate_policy(_receipt(**receipt), config)[0]


def test_required_tests_pass_warn_and_not_applicable() -> None:
    assert _evaluate("required_tests", {}).status == NOT_APPLICABLE
    passed = _evaluate("required_tests", {"commands": ["pytest"]}, evidence=["pytest -q passed"])
    assert passed.status == PASS
    missing = _evaluate("required_tests", {"commands": ["pytest", "ruff"]}, evidence=["pytest ok"])
    assert missing.status == FAIL
    assert "ruff" in missing.reason
    acknowledged = _evaluate(
        "required_tests", {"commands": ["ruff"]}, known_failures=["ruff flaky on CI runner"]
    )
    assert acknowledged.status == PASS


def test_strict_type_checking_paths() -> None:
    assert _evaluate("strict_type_checking", {}).status == NOT_APPLICABLE
    assert (
        _evaluate(
            "strict_type_checking", {"command": "mypy {files}"}, changed_files=["a.txt"]
        ).status
        == NOT_APPLICABLE
    )
    missing = _evaluate(
        "strict_type_checking",
        {"python": {"command": "mypy --strict {files}"}},
        changed_files=["a.py"],
    )
    assert missing.status == FAIL
    present = _evaluate(
        "strict_type_checking",
        {"command": "mypy --strict {files}"},
        changed_files=["a.py"],
        evidence=["mypy --strict clean"],
    )
    assert present.status == PASS


def test_owner_approval_paths() -> None:
    assert _evaluate("owner_approval", {}).status == NOT_APPLICABLE
    missing = _evaluate("owner_approval", {"owners": ["lead"]}, approvals=["alice"])
    assert missing.status == FAIL
    present = _evaluate("owner_approval", {"owners": ["lead"]}, approvals=["lead approved 2026"])
    assert present.status == PASS


def test_evidence_freshness_paths() -> None:
    assert _evaluate("evidence_freshness", {}).status == NOT_APPLICABLE
    unknown = _evaluate("evidence_freshness", {"max_age_seconds": 60})
    assert unknown.status == WARN
    stale = _evaluate("evidence_freshness", {"max_age_seconds": 60}, freshness_seconds=120.0)
    assert stale.status == WARN
    fresh = _evaluate("evidence_freshness", {"max_age_seconds": 600}, freshness_seconds=30.0)
    assert fresh.status == PASS


def test_no_merge_without_receipt_paths() -> None:
    assert _evaluate("no_merge_without_receipt", {}).status == NOT_APPLICABLE
    assert _evaluate("no_merge_without_receipt", {"required": True}).status == FAIL
    assert _evaluate("no_merge_without_receipt", {"required": True}, evidence=["x"]).status == PASS


def test_known_failure_acknowledgement_paths() -> None:
    assert _evaluate("known_failure_acknowledgement", {}).status == NOT_APPLICABLE
    vague = _evaluate("known_failure_acknowledgement", {}, known_failures=["x"])
    assert vague.status == WARN
    described = _evaluate(
        "known_failure_acknowledgement", {}, known_failures=["flaky network test, owner alice"]
    )
    assert described.status == PASS


def test_generated_artifact_parity_paths() -> None:
    assert _evaluate("generated_artifact_parity", {}).status == NOT_APPLICABLE
    warn = _evaluate("generated_artifact_parity", {}, changed_files=["a.py"])
    assert warn.status == WARN
    ok = _evaluate(
        "generated_artifact_parity",
        {},
        changed_files=["a.py"],
        generated_artifacts=["docs/_generated/x.json"],
    )
    assert ok.status == PASS


def test_unknown_rule_warns() -> None:
    decision = _evaluate("teleport_check", {})
    assert decision.status == WARN
    assert "unknown policy rule" in decision.reason


def test_evaluate_uses_receipt_task_id_as_subject() -> None:
    config = PolicyConfig(1, "advisory", {"no_merge_without_receipt": {"required": True}})
    decisions = evaluate_policy(_receipt(task_id="TASK-9"), config)
    assert decisions[0].subject == "TASK-9"


def test_non_mapping_rule_config_is_tolerated() -> None:
    config = PolicyConfig(1, "advisory", {"required_tests": "oops-not-a-dict"})
    decisions = evaluate_policy(_receipt(), config)
    assert decisions[0].status == NOT_APPLICABLE


def test_items_ignores_non_sequence_receipt_fields() -> None:
    decision = _evaluate("required_tests", {"commands": ["pytest"]}, evidence="not-a-list")
    assert decision.status == FAIL
