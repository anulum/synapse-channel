# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable auto-action policy store regressions

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from synapse_channel.participants.auto_action import AutoAction, AutoActionPolicy
from synapse_channel.participants.auto_action_store import (
    POLICY_FILENAME,
    STORE_VERSION,
    AutoActionStoreError,
    load_policy,
    save_policy,
)


def test_missing_file_arms_nothing(tmp_path: Path) -> None:
    # A store that was never written is not an error: it means the default, arm-nothing policy.
    policy = load_policy(tmp_path / "absent.json")

    assert policy == AutoActionPolicy()
    assert policy.armed == frozenset()


def test_save_then_load_round_trips_the_armed_set(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    saved = AutoActionPolicy(armed=frozenset({AutoAction.COMPACT, AutoAction.HANDOVER}))

    save_policy(path, saved)

    assert load_policy(path) == saved


def test_save_creates_the_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "home" / POLICY_FILENAME

    save_policy(path, AutoActionPolicy(armed=frozenset({AutoAction.LOG})))

    assert path.exists()
    assert load_policy(path).armed == frozenset({AutoAction.LOG})


def test_save_writes_owner_only_permissions(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"

    save_policy(path, AutoActionPolicy(armed=frozenset({AutoAction.LOG})))

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_is_stable_and_sorted(tmp_path: Path) -> None:
    # The same armed set produces a byte-identical file regardless of set iteration order.
    path = tmp_path / "policy.json"
    policy = AutoActionPolicy(armed=frozenset({AutoAction.LOG, AutoAction.COMPACT}))

    save_policy(path, policy)
    first = path.read_bytes()
    save_policy(path, policy)
    second = path.read_bytes()

    assert first == second
    document = json.loads(first)
    assert document == {"version": STORE_VERSION, "armed": ["compact", "log"]}
    assert document["armed"] == sorted(document["armed"])


def test_save_overwrites_an_existing_policy(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    save_policy(path, AutoActionPolicy(armed=frozenset({AutoAction.LOG, AutoAction.COMPACT})))

    save_policy(path, AutoActionPolicy(armed=frozenset({AutoAction.HANDOVER})))

    assert load_policy(path).armed == frozenset({AutoAction.HANDOVER})


def test_save_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"

    save_policy(path, AutoActionPolicy(armed=frozenset({AutoAction.COMPACT})))

    assert not (tmp_path / f"{path.name}.tmp").exists()
    assert list(tmp_path.iterdir()) == [path]


def test_empty_policy_persists_an_empty_list(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"

    save_policy(path, AutoActionPolicy())

    assert json.loads(path.read_text(encoding="utf-8"))["armed"] == []
    assert load_policy(path).armed == frozenset()


def test_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(AutoActionStoreError, match="not valid JSON"):
        load_policy(path)


def test_non_object_document_raises(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    with pytest.raises(AutoActionStoreError, match="must hold an object, not list"):
        load_policy(path)


def test_unsupported_version_raises(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"version": 99, "armed": []}), encoding="utf-8")

    with pytest.raises(AutoActionStoreError, match="unsupported version 99"):
        load_policy(path)


def test_missing_version_raises(tmp_path: Path) -> None:
    # A file with no version field is not a policy this build recognises.
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"armed": ["compact"]}), encoding="utf-8")

    with pytest.raises(AutoActionStoreError, match="unsupported version None"):
        load_policy(path)


def test_armed_field_must_be_a_list(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"version": STORE_VERSION, "armed": "compact"}), encoding="utf-8")

    with pytest.raises(AutoActionStoreError, match="must be a list, not str"):
        load_policy(path)


def test_missing_armed_field_defaults_to_empty(tmp_path: Path) -> None:
    # A versioned document with no 'armed' field is a valid empty policy, not a corruption.
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"version": STORE_VERSION}), encoding="utf-8")

    assert load_policy(path).armed == frozenset()


def test_non_string_action_raises(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"version": STORE_VERSION, "armed": [7]}), encoding="utf-8")

    with pytest.raises(AutoActionStoreError, match="non-string action"):
        load_policy(path)


def test_unknown_action_tag_raises(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps({"version": STORE_VERSION, "armed": ["compact", "teleport"]}), encoding="utf-8"
    )

    with pytest.raises(AutoActionStoreError, match="unknown action 'teleport'"):
        load_policy(path)


def test_duplicate_tags_collapse_to_one(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps({"version": STORE_VERSION, "armed": ["log", "log", "compact"]}),
        encoding="utf-8",
    )

    assert load_policy(path).armed == frozenset({AutoAction.LOG, AutoAction.COMPACT})
