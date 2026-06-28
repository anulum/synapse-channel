# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the deny-by-default ACL model and shadow evaluator

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel.core.acl import (
    CLAIM,
    MESSAGE,
    METRICS,
    WOULD_ALLOW,
    WOULD_DENY,
    AclError,
    AclPolicy,
    AclRule,
    Target,
    evaluate_access,
    load_acl_policy,
)


def _eval(policy: AclPolicy, permission: str, target: Target, project: str = "P") -> str:
    return evaluate_access(
        subject="P/agent", project=project, permission=permission, target=target, policy=policy
    ).decision


def test_allow_when_a_rule_matches_permission_kind_pattern_and_namespace() -> None:
    policy = AclPolicy([AclRule(CLAIM, "path", "src/*", "P", "core claim")])
    decision = evaluate_access(
        subject="P/agent",
        project="P",
        permission=CLAIM,
        target=Target("path", "src/a.py"),
        policy=policy,
    )
    assert decision.decision == WOULD_ALLOW
    assert decision.matched_rule == 0
    assert decision.reason == "core claim"


def test_deny_by_default_when_no_rule_matches() -> None:
    policy = AclPolicy([AclRule(CLAIM, "path", "src/*")])
    assert _eval(policy, METRICS, Target("metrics", "live")) == WOULD_DENY


def test_pattern_must_match_target_value() -> None:
    policy = AclPolicy([AclRule(CLAIM, "path", "src/*")])
    assert _eval(policy, CLAIM, Target("path", "tests/a.py")) == WOULD_DENY
    assert _eval(policy, CLAIM, Target("path", "src/a.py")) == WOULD_ALLOW


def test_target_kind_must_match() -> None:
    policy = AclPolicy([AclRule(MESSAGE, "agent", "*")])
    assert _eval(policy, MESSAGE, Target("channel", "ops")) == WOULD_DENY
    assert _eval(policy, MESSAGE, Target("agent", "anyone")) == WOULD_ALLOW


def test_namespace_constraint_scopes_a_rule() -> None:
    policy = AclPolicy([AclRule(CLAIM, "path", "*", "P")])
    assert _eval(policy, CLAIM, Target("path", "x"), project="P") == WOULD_ALLOW
    assert _eval(policy, CLAIM, Target("path", "x"), project="OTHER") == WOULD_DENY


def test_blank_namespace_matches_any_project() -> None:
    policy = AclPolicy([AclRule(CLAIM, "path", "*")])
    assert _eval(policy, CLAIM, Target("path", "x"), project="ANYTHING") == WOULD_ALLOW


def test_unknown_permission_is_denied() -> None:
    decision = evaluate_access(
        subject="P/a",
        project="P",
        permission="teleport",
        target=Target("path", "x"),
        policy=AclPolicy([]),
    )
    assert decision.decision == WOULD_DENY
    assert "unknown permission" in decision.reason


def test_first_matching_rule_wins_and_default_reason() -> None:
    policy = AclPolicy([AclRule(CLAIM, "path", "src/*"), AclRule(CLAIM, "path", "src/a.py")])
    decision = evaluate_access(
        subject="P/a",
        project="P",
        permission=CLAIM,
        target=Target("path", "src/a.py"),
        policy=policy,
    )
    assert decision.matched_rule == 0
    assert decision.reason == "granted by rule 0"


def test_decision_serialises_to_json() -> None:
    decision = evaluate_access(
        subject="P/a",
        project="P",
        permission=CLAIM,
        target=Target("path", "x"),
        policy=AclPolicy([]),
    )
    payload = decision.as_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["target"] == {"kind": "path", "value": "x"}


def test_load_acl_policy_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "acl.json"
    path.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "permission": "claim",
                        "target_kind": "path",
                        "target_pattern": "src/*",
                        "namespace": "P",
                        "reason": "core",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    policy = load_acl_policy(path)
    assert len(policy.rules) == 1
    assert policy.rules[0].reason == "core"


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("{}", "object with a 'rules' list"),
        ('{"rules": [1]}', "must be an object"),
        (
            '{"rules": [{"permission": "nope", "target_kind": "path", "target_pattern": "x"}]}',
            "unknown permission",
        ),
        (
            '{"rules": [{"permission": "claim", "target_kind": "", "target_pattern": "x"}]}',
            "non-empty target_kind",
        ),
        ("{not json", "invalid ACL JSON"),
    ],
)
def test_load_acl_policy_rejects_bad_files(tmp_path: Path, content: str, match: str) -> None:
    path = tmp_path / "acl.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(AclError, match=match):
        load_acl_policy(path)


def test_load_acl_policy_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AclError, match="does not exist"):
        load_acl_policy(tmp_path / "absent.json")
