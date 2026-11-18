# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the projection Core applies to a read row's JSON output, and its digest
"""Selection by projection paths, the refusals, and the digest as Core's canonical JSON."""

from __future__ import annotations

import hashlib
import json

import pytest

from synapse_channel.core.aef_canonical import canonical_json
from synapse_channel.operations.projection import (
    MAX_PATH_LENGTH,
    PROJECTION_PATH,
    ProjectionError,
    project,
    projection_digest,
)

OUTPUT = json.dumps(
    {
        "settings": {"theme": "dark", "size": 12, "updated_at": 1800000000.25},
        "plugins": [
            {"name": "lint", "enabled": True, "meta": {"id": 1}},
            {"name": "fmt", "enabled": False, "meta": {"id": 2}},
        ],
        "empty": [],
        "nothing": None,
        "scalar": 3,
    }
)


def test_paths_select_values_and_arrays_keep_their_order() -> None:
    paths = (
        "settings.theme",
        "plugins[].name",
        "plugins[].meta.id",
        "empty[]",
        "nothing",
        "settings",
    )
    selected = project(OUTPUT, paths[:-1])
    assert selected == {
        "settings.theme": "dark",
        "plugins[].name": ["lint", "fmt"],
        "plugins[].meta.id": [1, 2],
        "empty[]": [],
        "nothing": None,
    }
    assert list(selected) == list(paths[:-1])
    with pytest.raises(ProjectionError, match=r"settings selects a value outside Core"):
        project(OUTPUT, ("settings",))  # it holds a number with a fraction


def test_a_fraction_matters_only_when_selected() -> None:
    assert project(OUTPUT, ("settings.size",)) == {"settings.size": 12}
    with pytest.raises(ProjectionError, match=r"settings.updated_at selects a value outside"):
        project(OUTPUT, ("settings.updated_at",))


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("settings.colour", "settings.colour: the output has no colour"),
        ("plugins[].missing", "plugins[].missing: the output has no missing"),
        ("scalar.inner", "scalar.inner: inner is read from something that is not an object"),
        (
            "settings[].theme",
            "settings[].theme: settings[] steps into something that is not an array",
        ),
        ("plugins.name", "plugins.name: name is read from something that is not an object"),
    ],
)
def test_a_path_the_output_does_not_have_is_a_refusal(path: str, reason: str) -> None:
    with pytest.raises(ProjectionError) as refused:
        project(OUTPUT, (path,))
    assert str(refused.value) == reason


@pytest.mark.parametrize(
    ("output", "truncated", "paths", "reason"),
    [
        (OUTPUT, True, ("scalar",), "the output was cut at the runner's limit"),
        (OUTPUT, False, (), "the row names no projection paths"),
        ("not json", False, ("scalar",), "the output is not one JSON value"),
        ('{"a": 1} {"b": 2}', False, ("a",), "the output is not one JSON value"),
        ('{"a": 1, "a": 2}', False, ("a",), "names the key 'a' twice in one object"),
        ('{"a": NaN}', False, ("a",), "the output holds NaN"),
        ('{"a": -Infinity}', False, ("a",), "the output holds -Infinity"),
        ("[" * 100_000 + "]" * 100_000, False, ("a",), "the output is not one JSON value"),
        ('[{"a": 1}]', False, ("a",), "a: a is read from something that is not an object"),
    ],
)
def test_an_output_that_gives_no_projection_is_refused(
    output: str, truncated: bool, paths: tuple[str, ...], reason: str
) -> None:
    with pytest.raises(ProjectionError, match=reason.replace("(", r"\(").replace(")", r"\)")):
        project(output, paths, truncated=truncated)


def test_the_digest_is_core_canonical_json_whatever_the_order_of_keys() -> None:
    first = project('{"b": {"y": 2, "x": 1}, "a": "z"}', ("a", "b"))
    second = project('{"a": "z", "b": {"x": 1, "y": 2}}', ("b", "a"))
    expected = hashlib.sha256(canonical_json({"a": "z", "b": {"x": 1, "y": 2}})).hexdigest()
    assert projection_digest(first) == projection_digest(second) == expected
    assert len(expected) == 64 and expected == expected.lower()
    assert projection_digest({"a": [1, 2]}) != projection_digest({"a": [2, 1]})


def test_the_path_grammar() -> None:
    accepted = ["a", "a.b", "a[]", "a[].b[].c", "A_b-9", ".".join(["k"] * 16)]
    refused = ["", "a.", ".a", "a..b", "a[0]", "a[]x", "a.*", "a b", ".".join(["k"] * 17), "k" * 65]
    assert all(PROJECTION_PATH.fullmatch(path) for path in accepted)
    assert not any(PROJECTION_PATH.fullmatch(path) for path in refused)
    assert MAX_PATH_LENGTH == 256
