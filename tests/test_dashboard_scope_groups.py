# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the dashboard's declared groups of namespaces

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from synapse_channel.dashboard_scope_groups import (
    MAX_GROUP_NAMESPACES,
    MAX_SCOPE_GROUPS,
    MAX_SCOPE_GROUPS_BYTES,
    SCOPE_GROUPS_FLAG,
    load_scope_groups,
    parse_scope_groups,
    valid_scope_name,
)


def _write(path: Path, document: object, *, mode: int = 0o600) -> Path:
    path.write_text(document if isinstance(document, str) else json.dumps(document))
    path.chmod(mode)
    return path


def test_a_groups_file_names_each_group_and_its_namespaces(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "groups.json",
        {"version": 1, "groups": {"ships": ["harbour", "lighthouse"], "harbour": ["harbour"]}},
    )
    groups = load_scope_groups(path)

    assert groups.names() == ["harbour", "ships"]
    assert groups.members("ships") == frozenset({"harbour", "lighthouse"})
    # A namespace may be in two groups: each group is a view of its own.
    assert groups.members("harbour") == frozenset({"harbour"})
    assert groups.members("absent") is None


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("{", "is not JSON"),
        ("[]", "needs exactly version and groups"),
        ('{"version": 1}', "needs exactly version and groups"),
        ('{"version": 1, "groups": {"a": ["x"]}, "extra": 1}', "needs exactly version and groups"),
        ('{"version": 2, "groups": {"a": ["x"]}}', "version must be 1"),
        ('{"version": true, "groups": {"a": ["x"]}}', "version must be 1"),
        ('{"version": "1", "groups": {"a": ["x"]}}', "version must be 1"),
        ('{"version": 1, "groups": []}', "groups must map"),
        ('{"version": 1, "groups": {}}', "groups must map"),
        ('{"version": 1, "version": 1, "groups": {"a": ["x"]}}', "repeats the key 'version'"),
        ('{"version": 1, "groups": {"a": ["x"], "a": ["y"]}}', "repeats the key 'a'"),
        ('{"version": 1, "groups": {"a/b": ["x"]}}', "a group name must be"),
        ('{"version": 1, "groups": {"": ["x"]}}', "a group name must be"),
        ('{"version": 1, "groups": {"a": "x"}}', "needs a list of 1 to"),
        ('{"version": 1, "groups": {"a": []}}', "needs a list of 1 to"),
        ('{"version": 1, "groups": {"a": ["x/y"]}}', "names a namespace a scope cannot carry"),
        ('{"version": 1, "groups": {"a": [7]}}', "names a namespace a scope cannot carry"),
        ('{"version": 1, "groups": {"a": ["x", "x"]}}', "names a namespace twice"),
    ],
)
def test_a_groups_file_of_another_shape_is_refused_naming_the_flag(text: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason) as refused:
        parse_scope_groups(text)
    assert str(refused.value).startswith(f"{SCOPE_GROUPS_FLAG}: ")


def test_the_bounds_of_a_groups_file_are_its_limits() -> None:
    namespaces = [f"ns{index}" for index in range(MAX_GROUP_NAMESPACES)]
    many = {f"g{index}": ["x"] for index in range(MAX_SCOPE_GROUPS)}
    widest = parse_scope_groups(json.dumps({"version": 1, "groups": {"wide": namespaces}}))

    assert len(widest.members("wide") or ()) == MAX_GROUP_NAMESPACES
    assert len(parse_scope_groups(json.dumps({"version": 1, "groups": many})).names()) == 256
    with pytest.raises(ValueError, match="needs a list of 1 to 256"):
        parse_scope_groups(json.dumps({"version": 1, "groups": {"wide": [*namespaces, "one"]}}))
    with pytest.raises(ValueError, match="groups must map 1 to 256"):
        parse_scope_groups(json.dumps({"version": 1, "groups": {**many, "one": ["x"]}}))


def test_a_scope_name_is_printable_bounded_and_holds_no_slash() -> None:
    assert valid_scope_name("x" * 256)
    assert not valid_scope_name("x" * 257)
    assert not valid_scope_name("tab\there")
    assert not valid_scope_name("")
    assert not valid_scope_name(None)


def test_a_groups_file_others_can_read_is_refused_without_its_content(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "groups.json", {"version": 1, "groups": {"secret-name": ["x"]}}, mode=0o644
    )

    with pytest.raises(ValueError, match=SCOPE_GROUPS_FLAG) as refused:
        load_scope_groups(path)
    assert "secret-name" not in str(refused.value)


def test_a_groups_file_with_a_second_link_or_too_large_or_missing_is_refused(
    tmp_path: Path,
) -> None:
    linked = _write(tmp_path / "groups.json", {"version": 1, "groups": {"a": ["x"]}})
    os.link(linked, tmp_path / "alias.json")
    large = _write(tmp_path / "large.json", " " * (MAX_SCOPE_GROUPS_BYTES + 1))

    for path in (linked, large, tmp_path / "missing.json"):
        with pytest.raises(ValueError, match=SCOPE_GROUPS_FLAG):
            load_scope_groups(path)
