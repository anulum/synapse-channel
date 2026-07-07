# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the deny-by-default role-claim grant store

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from synapse_channel.core.role_grants import (
    STORE_FILE_MODE,
    RoleGrantError,
    RoleGrants,
    load_role_grants,
    save_role_grants,
)


def _grants(**mapping: set[str]) -> RoleGrants:
    return RoleGrants({role: frozenset(subjects) for role, subjects in mapping.items()})


class TestMayClaim:
    def test_exact_subject_is_permitted(self) -> None:
        grants = _grants(**{"SYNAPSE-CHANNEL/coordinator": {"SYNAPSE-CHANNEL/claude-2759"}})

        assert grants.may_claim("SYNAPSE-CHANNEL/claude-2759", "SYNAPSE-CHANNEL/coordinator")

    def test_glob_subject_is_permitted(self) -> None:
        grants = _grants(**{"SYNAPSE-CHANNEL/coordinator": {"SYNAPSE-CHANNEL/*"}})

        assert grants.may_claim("SYNAPSE-CHANNEL/claude-2759", "SYNAPSE-CHANNEL/coordinator")

    def test_glob_does_not_leak_across_namespaces(self) -> None:
        grants = _grants(**{"SYNAPSE-CHANNEL/coordinator": {"SYNAPSE-CHANNEL/*"}})

        assert not grants.may_claim("REMANENTIA/evil", "SYNAPSE-CHANNEL/coordinator")

    def test_matching_is_case_sensitive(self) -> None:
        grants = _grants(**{"SYNAPSE-CHANNEL/coordinator": {"SYNAPSE-CHANNEL/Claude"}})

        assert not grants.may_claim("SYNAPSE-CHANNEL/claude", "SYNAPSE-CHANNEL/coordinator")

    def test_ungranted_role_is_denied(self) -> None:
        assert not RoleGrants({}).may_claim("a/b", "a/coordinator")

    def test_role_with_empty_grant_set_is_denied(self) -> None:
        grants = _grants(**{"a/coordinator": set()})

        assert not grants.may_claim("a/b", "a/coordinator")

    def test_blank_subject_is_denied(self) -> None:
        grants = _grants(**{"a/coordinator": {"a/b"}})

        assert not grants.may_claim("", "a/coordinator")


class TestAuthorisedRoles:
    def test_filters_to_permitted_and_preserves_order(self) -> None:
        grants = _grants(
            **{"a/one": {"a/me"}, "a/three": {"a/me"}},
        )

        assert grants.authorised_roles("a/me", ["a/one", "a/two", "a/three"]) == (
            "a/one",
            "a/three",
        )

    def test_none_permitted_yields_empty(self) -> None:
        assert RoleGrants({}).authorised_roles("a/me", ["a/one", "a/two"]) == ()


class TestQueries:
    def test_roles_are_sorted(self) -> None:
        grants = _grants(**{"a/z": {"a/x"}, "a/a": {"a/x"}})

        assert grants.roles() == ("a/a", "a/z")

    def test_subjects_for_are_sorted(self) -> None:
        grants = _grants(**{"a/r": {"a/z", "a/a"}})

        assert grants.subjects_for("a/r") == ("a/a", "a/z")

    def test_subjects_for_unknown_role_is_empty(self) -> None:
        assert RoleGrants({}).subjects_for("a/r") == ()


class TestMutations:
    def test_with_grant_adds_a_subject(self) -> None:
        grants = RoleGrants({}).with_grant("a/coordinator", "a/me")

        assert grants.subjects_for("a/coordinator") == ("a/me",)

    def test_with_grant_is_idempotent(self) -> None:
        once = RoleGrants({}).with_grant("a/coordinator", "a/me")
        twice = once.with_grant("a/coordinator", "a/me")

        assert twice == once

    def test_with_grant_extends_an_existing_role(self) -> None:
        grants = (
            RoleGrants({}).with_grant("a/coordinator", "a/me").with_grant("a/coordinator", "a/you")
        )

        assert grants.subjects_for("a/coordinator") == ("a/me", "a/you")

    def test_with_grant_does_not_mutate_the_original(self) -> None:
        original = RoleGrants({})
        original.with_grant("a/coordinator", "a/me")

        assert original.roles() == ()

    def test_with_grant_rejects_a_role_without_a_slash(self) -> None:
        with pytest.raises(RoleGrantError, match="<project>/<role>"):
            RoleGrants({}).with_grant("coordinator", "a/me")

    def test_with_grant_rejects_a_role_with_a_blank_half(self) -> None:
        with pytest.raises(RoleGrantError, match="<project>/<role>"):
            RoleGrants({}).with_grant("a/", "a/me")

    def test_with_grant_rejects_a_blank_subject(self) -> None:
        with pytest.raises(RoleGrantError, match="subject"):
            RoleGrants({}).with_grant("a/coordinator", "   ")

    def test_without_grant_removes_a_subject(self) -> None:
        grants = (
            RoleGrants({})
            .with_grant("a/coordinator", "a/me")
            .with_grant("a/coordinator", "a/you")
            .without_grant("a/coordinator", "a/me")
        )

        assert grants.subjects_for("a/coordinator") == ("a/you",)

    def test_without_grant_collapses_an_emptied_role(self) -> None:
        grants = (
            RoleGrants({})
            .with_grant("a/coordinator", "a/me")
            .without_grant("a/coordinator", "a/me")
        )

        assert grants.roles() == ()

    def test_without_grant_absent_is_a_noop(self) -> None:
        grants = RoleGrants({}).with_grant("a/coordinator", "a/me")

        assert grants.without_grant("a/coordinator", "a/nobody") == grants

    def test_without_grant_unknown_role_is_a_noop(self) -> None:
        assert RoleGrants({}).without_grant("a/coordinator", "a/me") == RoleGrants({})

    def test_without_grant_rejects_a_malformed_role(self) -> None:
        with pytest.raises(RoleGrantError, match="<project>/<role>"):
            RoleGrants({}).without_grant("noslash", "a/me")

    def test_without_grant_rejects_a_blank_subject(self) -> None:
        with pytest.raises(RoleGrantError, match="subject"):
            RoleGrants({}).without_grant("a/coordinator", "  ")


class TestJson:
    def test_to_json_obj_is_sorted_and_shaped(self) -> None:
        grants = _grants(**{"a/z": {"a/y", "a/x"}, "a/a": {"a/b"}})

        assert grants.to_json_obj() == {
            "grants": {"a/a": ["a/b"], "a/z": ["a/x", "a/y"]},
        }


class TestLoad:
    def test_absent_file_is_an_empty_store(self, tmp_path: Path) -> None:
        assert load_role_grants(tmp_path / "missing.json") == RoleGrants({})

    def test_valid_file_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "store.json"
        path.write_text(
            json.dumps({"grants": {"a/coordinator": ["a/me", "a/you"]}}), encoding="utf-8"
        )

        grants = load_role_grants(path)

        assert grants.subjects_for("a/coordinator") == ("a/me", "a/you")

    def test_expands_a_home_relative_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        store = tmp_path / ".synapse" / "role-grants.json"
        store.parent.mkdir(parents=True)
        store.write_text(json.dumps({"grants": {"a/r": ["a/me"]}}), encoding="utf-8")

        assert load_role_grants("~/.synapse/role-grants.json").subjects_for("a/r") == ("a/me",)

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "store.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(RoleGrantError, match="not valid JSON"):
            load_role_grants(path)

    def test_top_level_must_be_a_mapping_with_grants(self, tmp_path: Path) -> None:
        path = tmp_path / "store.json"
        path.write_text(json.dumps(["nope"]), encoding="utf-8")

        with pytest.raises(RoleGrantError, match="mapping with a 'grants' mapping"):
            load_role_grants(path)

    def test_grants_must_be_a_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "store.json"
        path.write_text(json.dumps({"grants": ["nope"]}), encoding="utf-8")

        with pytest.raises(RoleGrantError, match="mapping with a 'grants' mapping"):
            load_role_grants(path)

    def test_subjects_must_be_a_list(self, tmp_path: Path) -> None:
        path = tmp_path / "store.json"
        path.write_text(json.dumps({"grants": {"a/r": "a/me"}}), encoding="utf-8")

        with pytest.raises(RoleGrantError, match="must be a list"):
            load_role_grants(path)

    def test_malformed_role_key_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "store.json"
        path.write_text(json.dumps({"grants": {"noslash": ["a/me"]}}), encoding="utf-8")

        with pytest.raises(RoleGrantError, match="<project>/<role>"):
            load_role_grants(path)

    def test_blank_subject_entry_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "store.json"
        path.write_text(json.dumps({"grants": {"a/r": [""]}}), encoding="utf-8")

        with pytest.raises(RoleGrantError, match="subject"):
            load_role_grants(path)

    def test_empty_subject_list_role_is_dropped(self, tmp_path: Path) -> None:
        path = tmp_path / "store.json"
        path.write_text(
            json.dumps({"grants": {"a/empty": [], "a/real": ["a/me"]}}), encoding="utf-8"
        )

        grants = load_role_grants(path)

        assert grants.roles() == ("a/real",)


class TestSave:
    def test_writes_a_loadable_store(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "store.json"
        grants = RoleGrants({}).with_grant("a/coordinator", "a/me")

        save_role_grants(path, grants)

        assert load_role_grants(path) == grants

    def test_written_file_is_owner_only(self, tmp_path: Path) -> None:
        path = tmp_path / "store.json"

        save_role_grants(path, RoleGrants({}).with_grant("a/r", "a/me"))

        assert stat.S_IMODE(path.stat().st_mode) == STORE_FILE_MODE

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "a" / "b" / "store.json"

        save_role_grants(path, RoleGrants({}))

        assert path.is_file()

    def test_expands_a_home_relative_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))

        save_role_grants("~/.synapse/role-grants.json", RoleGrants({}).with_grant("a/r", "a/me"))

        assert (tmp_path / ".synapse" / "role-grants.json").is_file()

    def test_unwritable_parent_raises_and_is_wrapped(self, tmp_path: Path) -> None:
        blocker = tmp_path / "afile"
        blocker.write_text("x", encoding="utf-8")

        with pytest.raises(RoleGrantError, match="cannot write role-grant store"):
            save_role_grants(blocker / "sub" / "store.json", RoleGrants({}))

    def test_replace_failure_cleans_up_the_temp_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "store.json"

        def _boom(src: object, dst: object) -> None:
            raise RuntimeError("replace failed")

        monkeypatch.setattr(os, "replace", _boom)

        with pytest.raises(RuntimeError, match="replace failed"):
            save_role_grants(path, RoleGrants({}).with_grant("a/r", "a/me"))

        assert list(tmp_path.glob("*.tmp")) == []
