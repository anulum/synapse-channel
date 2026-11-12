# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the authorisation intent a declaration can carry
"""An intent is validated per effect, composed with the hub's fields and digested canonically."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from synapse_channel.core.authorisation_intent import (
    INTENT_SCHEMA,
    MAX_DEADLINE_AHEAD,
    MAX_INTENT_BYTES,
    REFUSED_INTENT_EXPIRED,
    REFUSED_INTENT_INVALID,
    AuthorisationIntentError,
    compose_intent,
    intent_time_refusal,
    parse_intent_statement,
)

HEX64 = "a" * 64
HEX40 = "b" * 40
NOW = 1_800_000_000


def _statement(**changes: Any) -> dict[str, Any]:
    """Return a valid file-effect statement with ``changes`` applied (``None`` removes)."""
    document: dict[str, Any] = {
        "schema": INTENT_SCHEMA,
        "pre_state": "absent",
        "recovery_class": "reversible",
        "deadline": NOW + 3600,
        "budget": "not_applicable",
        "egress": [],
        "tool": {"name": "render-report", "version": "1.2.0"},
        "arguments_sha256": HEX64,
        "rule_generation": "prose-rules:2026-11-12T06:16:35Z",
        "required_assurance": "cooperative",
    }
    for key, value in changes.items():
        if value is None:
            document.pop(key, None)
        else:
            document[key] = value
    return document


def _git_statement(**changes: Any) -> dict[str, Any]:
    files = [{"path": "README.md", "mode": "100644"}, {"path": "tools/run.sh", "mode": "100755"}]
    changes.setdefault("changed_files", files)
    return _statement(pre_state=HEX40, **changes)


def _compose(statement: dict[str, Any], effect: str = "file") -> Any:
    targets = {
        "file": {"path": "out/report.txt", "sha256": HEX64},
        "git_publication": {"repository": "origin", "branch": "main", "revision": "c" * 40},
    }
    return compose_intent(
        parse_intent_statement(statement, effect),
        task_id="T1",
        operation_id="op-1",
        effect=effect,
        target=targets[effect],
        actor="P/worker",
    )


def test_a_file_intent_composes_with_the_hub_fields_and_a_canonical_digest() -> None:
    """The digest is SHA-256 over sorted, compact JSON of the statement and the hub's fields."""
    intent = _compose(_statement())
    expected = {
        **_statement(),
        "task_id": "T1",
        "operation_id": "op-1",
        "effect": "file",
        "effect_target": {"path": "out/report.txt", "sha256": HEX64},
        "actor": "P/worker",
    }
    oracle = json.dumps(expected, sort_keys=True, separators=(",", ":")).encode("ascii")
    assert intent.document == expected
    assert intent.canonical == oracle
    assert intent.sha256 == hashlib.sha256(oracle).hexdigest()


def test_the_same_statement_gives_the_same_digest_and_any_change_gives_another() -> None:
    """A reviewer recomputes the digest; one changed field or a different actor changes it."""
    first, second = _compose(_statement()), _compose(_statement())
    assert first.sha256 == second.sha256
    assert _compose(_statement(deadline=NOW + 3601)).sha256 != first.sha256
    other_actor = compose_intent(
        parse_intent_statement(_statement(), "file"),
        task_id="T1",
        operation_id="op-1",
        effect="file",
        target={"path": "out/report.txt", "sha256": HEX64},
        actor="P/other",
    )
    assert other_actor.sha256 != first.sha256


def test_a_git_intent_names_its_base_and_its_changed_files() -> None:
    """A git publication starts from a base commit and lists every changed path with its mode."""
    intent = _compose(_git_statement(tool_descriptor_sha256="d" * 64), "git_publication")
    assert intent.document["pre_state"] == HEX40
    assert intent.document["changed_files"][1] == {"path": "tools/run.sh", "mode": "100755"}
    assert intent.document["tool_descriptor_sha256"] == "d" * 64
    deleted = [{"path": "old.txt", "mode": "absent"}]
    assert (
        _compose(_git_statement(changed_files=deleted), "git_publication").document["changed_files"]
        == deleted
    )


@pytest.mark.parametrize(
    ("effect", "pre_state"),
    [
        ("file", "absent"),
        ("file", HEX64),
        ("deployment", "build-2026.11.12"),
        ("migration", HEX64),
    ],
)
def test_each_effect_accepts_its_own_pre_state(effect: str, pre_state: str) -> None:
    """The pre-state's form follows the declared effect."""
    statement = parse_intent_statement(_statement(pre_state=pre_state), effect)
    assert statement.document["pre_state"] == pre_state


@pytest.mark.parametrize(
    ("effect", "pre_state"),
    [
        ("file", HEX40),
        ("file", "ABSENT"),
        ("git_publication", "absent"),
        ("git_publication", HEX64),
        ("deployment", ""),
        ("deployment", "x" * 129),
        ("deployment", "line\nbreak"),
        ("migration", "absent"),
        ("file", 7),
    ],
)
def test_a_pre_state_of_the_wrong_form_is_refused(effect: str, pre_state: object) -> None:
    """A pre-state that does not fit the effect is refused without echoing the value."""
    statement = _statement(pre_state=pre_state)
    if effect == "git_publication":
        statement["changed_files"] = [{"path": "a", "mode": "100644"}]
    with pytest.raises(AuthorisationIntentError) as refused:
        parse_intent_statement(statement, effect)
    assert "line\nbreak" not in str(refused.value)


@pytest.mark.parametrize(
    "changed_files",
    [
        None,
        [],
        [{"path": "a", "mode": "100600"}],
        [{"path": "/etc/passwd", "mode": "100644"}],
        [{"path": "a/../b", "mode": "100644"}],
        [{"path": "a//b", "mode": "100644"}],
        [{"path": "./a", "mode": "100644"}],
        [{"path": "a\tb", "mode": "100644"}],
        [{"path": "b", "mode": "100644"}, {"path": "a", "mode": "100644"}],
        [{"path": "a", "mode": "100644"}, {"path": "a", "mode": "100755"}],
        [{"path": "a", "mode": "100644", "size": 1}],
        ["a"],
        "a",
    ],
)
def test_malformed_changed_files_are_refused(changed_files: object) -> None:
    """Paths are relative, plain and sorted once each; modes come from Git's own set."""
    with pytest.raises(AuthorisationIntentError):
        parse_intent_statement(_git_statement(changed_files=changed_files), "git_publication")


def test_an_unknown_effect_has_no_pre_state() -> None:
    """A caller that passes an effect the declaration does not know is refused."""
    with pytest.raises(AuthorisationIntentError, match="effect is unknown"):
        parse_intent_statement(_statement(), "teleport")


def test_changed_files_belong_to_a_git_publication_only() -> None:
    """Another effect that lists changed files is refused."""
    files = [{"path": "a", "mode": "100644"}]
    with pytest.raises(AuthorisationIntentError, match="changed_files"):
        parse_intent_statement(_statement(changed_files=files), "file")
    too_many = [{"path": f"f{index:04d}", "mode": "100644"} for index in range(1001)]
    with pytest.raises(AuthorisationIntentError, match="changed_files"):
        parse_intent_statement(_git_statement(changed_files=too_many), "git_publication")


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "synapse.authorization-intent.v0"},
        {"schema": None},
        {"recovery_class": "irreversible"},
        {"deadline": 1.5},
        {"deadline": True},
        {"deadline": 0},
        {"deadline": 2**53},
        {"budget": "reservation:"},
        {"budget": "reservation:has space"},
        {"budget": "unknown"},
        {"egress": ["https://example.com"]},
        {"egress": ["https://b.example:443", "https://a.example:443"]},
        {"egress": ["https://a.example:443", "https://a.example:443"]},
        {"egress": ["ftp://a.example:21"]},
        {"egress": [f"https://h{index:02d}.example:443" for index in range(17)]},
        {"egress": "https://a.example:443"},
        {"tool": {"name": "x"}},
        {"tool": {"name": "x", "version": "1", "path": "/bin/x"}},
        {"tool": {"name": "", "version": "1"}},
        {"tool": {"name": "x", "version": "v\x7f"}},
        {"tool": "x"},
        {"arguments_sha256": "A" * 64},
        {"arguments_sha256": None},
        {"tool_descriptor_sha256": "short"},
        {"rule_generation": "sha256:" + "a" * 63},
        {"rule_generation": "prose-rules:2026-13-40T06:16:35Z"},
        {"rule_generation": "prose-rules:2026-11-12T06:16:35+00:00"},
        {"rule_generation": "latest"},
        {"required_assurance": "trusted"},
        {"extra": 1},
    ],
)
def test_every_malformed_field_is_refused(changes: dict[str, Any]) -> None:
    """Each field has one closed form; anything else is refused."""
    with pytest.raises(AuthorisationIntentError):
        parse_intent_statement(_statement(**changes), "file")


@pytest.mark.parametrize("field", ["actor", "task_id", "operation_id", "effect", "effect_target"])
def test_the_fields_the_hub_fills_cannot_be_supplied(field: str) -> None:
    """The declarer never names the actor or repeats the declaration's own identifiers."""
    with pytest.raises(AuthorisationIntentError, match="the hub fills"):
        parse_intent_statement(_statement(**{field: "P/someone-else"}), "file")


def test_a_valid_egress_list_and_reservation_are_kept() -> None:
    """Origins in their canonical form, sorted, and a reservation reference pass unchanged."""
    origins = ["http://127.0.0.1:8080", "https://api.example.com:443"]
    statement = parse_intent_statement(
        _statement(egress=origins, budget="reservation:pool-7.q1"), "file"
    )
    assert statement.document["egress"] == origins
    assert statement.document["budget"] == "reservation:pool-7.q1"


def test_a_statement_that_is_not_an_object_or_is_too_large_is_refused() -> None:
    """The statement is one bounded JSON object."""
    with pytest.raises(AuthorisationIntentError):
        parse_intent_statement(["not", "an", "object"], "file")
    files = [{"path": f"d/{'p' * 400}{index:04d}", "mode": "100644"} for index in range(200)]
    with pytest.raises(AuthorisationIntentError, match=str(MAX_INTENT_BYTES)):
        _compose(_git_statement(changed_files=files), "git_publication")


def _git_intent_of_size(target: int) -> Any:
    """Compose a git intent of 1,000 changed paths whose canonical bytes number ``target``."""
    entries = [{"path": f"d{index:04d}", "mode": "100644"} for index in range(1000)]
    gap = target - len(_compose(_git_statement(changed_files=entries), "git_publication").canonical)
    for entry in reversed(entries):
        grow = min(gap, 500)
        entry["path"] += "x" * grow
        gap -= grow
    assert gap == 0
    return _compose(_git_statement(changed_files=entries), "git_publication")


def test_the_byte_bound_wins_over_the_entry_count() -> None:
    """A full list of changed paths fits up to the byte bound and is refused one byte past it."""
    edge = _git_intent_of_size(MAX_INTENT_BYTES)
    assert len(edge.canonical) == MAX_INTENT_BYTES
    assert len(edge.document["changed_files"]) == 1000
    with pytest.raises(AuthorisationIntentError) as refused:
        _git_intent_of_size(MAX_INTENT_BYTES + 1)
    assert str(refused.value) == "authorisation intent: the canonical intent exceeds 65536 bytes"


def test_text_that_has_no_canonical_form_is_refused_when_composed() -> None:
    """A lone surrogate passes the character check but has no RFC 8785 form."""
    lone = _statement(tool={"name": "writer\ud800", "version": "1"})
    with pytest.raises(AuthorisationIntentError, match="canonically"):
        _compose(lone)


def test_the_deadline_must_lie_ahead_and_not_too_far() -> None:
    """A deadline at or before the hub's clock has expired; one beyond the bound is invalid."""
    statement = parse_intent_statement(_statement(), "file")
    assert intent_time_refusal(statement, NOW) is None
    assert intent_time_refusal(statement, NOW + 3600) == REFUSED_INTENT_EXPIRED
    far = parse_intent_statement(_statement(deadline=NOW + MAX_DEADLINE_AHEAD + 1), "file")
    assert intent_time_refusal(far, NOW) == REFUSED_INTENT_INVALID
    edge = parse_intent_statement(_statement(deadline=NOW + MAX_DEADLINE_AHEAD), "file")
    assert intent_time_refusal(edge, NOW) is None
    assert intent_time_refusal(statement, NOW + 0.5) is None
