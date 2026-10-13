# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the registered words of operation names
"""The vocabulary: which names exist, and what a vocabulary file must be."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import pytest

from synapse_channel.operations.spec import OperationSpecError
from synapse_channel.operations.vocabulary import SCHEMA, Vocabulary, packaged


def _data() -> dict[str, Any]:
    resource = files("synapse_channel").joinpath("operations").joinpath("vocabulary.json")
    data = json.loads(resource.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_the_packaged_vocabulary_is_the_file_beside_the_module() -> None:
    words = packaged()

    assert words is packaged()
    assert words == Vocabulary.from_json(_data())
    assert _data()["schema"] == SCHEMA == "operation-vocabulary-v1"
    assert words.revision == _data()["revision"] >= 1
    # The four objects the first inventory had no word for, kept apart from their neighbours.
    for word, apart_from in (
        ("skill", "plugin"),
        ("agent_definition", "plugin"),
        ("worktree", "project"),
        ("remote_task", "session"),
    ):
        assert word in words.objects and words.objects[word] != words.objects[apart_from]
    assert set(words.aspects) <= set(words.objects)
    assert all(meaning.strip() for meaning in words.actions.values())


def test_a_name_is_two_or_three_registered_words() -> None:
    words = packaged()

    assert words.parts("session.list") == ("session", "", "list")
    assert words.parts("plugin.marketplace.list") == ("plugin", "marketplace", "list")
    for name, message in (
        ("session", "object.action or object.aspect.action"),
        ("a.b.c.d", "object.action or object.aspect.action"),
        (None, "object.action or object.aspect.action"),
        ("session..list", "object.action or object.aspect.action"),
        ("setting..read", "object.action or object.aspect.action"),
        (".list", "object.action or object.aspect.action"),
        ("session.", "object.action or object.aspect.action"),
        ("thread.list", "thread.list: the object thread is not registered"),
        ("session.fork", "session.fork: the action fork is not registered"),
        ("session.marketplace.list", "the aspect marketplace is not registered for session"),
        ("skill.marketplace.list", "the aspect marketplace is not registered for skill"),
        ("session.list.read", "the aspect list is not registered for session"),
    ):
        with pytest.raises(OperationSpecError, match=message):
            words.parts(name)


def test_the_registered_words_cannot_be_changed_in_place() -> None:
    words = packaged()

    for mapping in (words.objects, words.actions, words.aspects, words.aspects["plugin"]):
        with pytest.raises(TypeError):
            cast(dict[str, str], mapping)["thread"] = "a vendor's word"


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (lambda data: [data], "not an operation vocabulary of this schema"),
        (lambda data: {**data, "schema": "other"}, "not an operation vocabulary of this schema"),
        (lambda data: {**data, "note": "x"}, "exactly its five fields"),
        (lambda data: {k: v for k, v in data.items() if k != "actions"}, "exactly its five"),
        (lambda data: {**data, "revision": 0}, "revision is a whole number from one"),
        (lambda data: {**data, "revision": True}, "revision is a whole number from one"),
        (lambda data: {**data, "revision": "1"}, "revision is a whole number from one"),
        (lambda data: {**data, "objects": ["session"]}, "objects map at most 256 words"),
        (
            lambda data: {**data, "actions": {f"a{i}": "x" for i in range(257)}},
            "actions map at most 256 words",
        ),
        (lambda data: {**data, "objects": {"Session": "x"}}, "among the objects is not a lower"),
        (lambda data: {**data, "actions": {"list": ""}}, "list among the actions has no bounded"),
        (lambda data: {**data, "actions": {"list": " "}}, "list among the actions has no bounded"),
        (lambda data: {**data, "actions": {"list": 3}}, "list among the actions has no bounded"),
        (lambda data: {**data, "actions": {"list": "x" * 301}}, "has no bounded meaning"),
        (lambda data: {**data, "aspects": []}, "aspects are registered per registered object"),
        (lambda data: {**data, "aspects": {"thread": {}}}, "aspects are registered per"),
        (
            lambda data: {**data, "aspects": {"session": {"Goal": "x"}}},
            "among the aspects of session is not a lower-case word",
        ),
    ],
)
def test_a_file_that_is_not_a_vocabulary_is_refused(damage: Any, message: str) -> None:
    with pytest.raises(OperationSpecError, match=message):
        Vocabulary.from_json(damage(_data()))


def test_the_packaging_rule_ships_the_vocabulary_file() -> None:
    """Without this rule an installed package has no vocabulary and reads no catalogue."""
    packaging = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    _, _, after = packaging.partition("[tool.setuptools.package-data]")
    rule, _, _ = after.partition("\n[")

    assert '"operations/*.json"' in rule
