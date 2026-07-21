# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — duplicate-safe workflow YAML primitive tests
"""Exercise the shared duplicate-safe YAML primitives for OpenCode gates."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from tools.opencode_workflow_yaml import (
        contains_mapping_key,
        load_workflow_yaml,
        require_object,
    )
else:
    # tools/ is not an installed package surface; mirror the contract test import
    # so focused CI can collect without PYTHONPATH=.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.opencode_workflow_yaml import (
        contains_mapping_key,
        load_workflow_yaml,
        require_object,
    )


class _Error(ValueError):
    """Distinct error type so tests confirm the injected taxonomy is used."""


def _load(text: str) -> Mapping[str, Any]:
    return load_workflow_yaml(text, Path("wf.yml"), error_cls=_Error, label="workflow")


def test_require_object_returns_mapping_unchanged() -> None:
    value = {"a": 1}
    assert require_object(value, "doc", error_cls=_Error) is value


def test_require_object_rejects_non_mapping() -> None:
    with pytest.raises(_Error, match="doc must be an object"):
        require_object(["a"], "doc", error_cls=_Error)


def test_load_returns_single_mapping_document() -> None:
    assert _load("jobs:\n  build: {}\n") == {"jobs": {"build": {}}}


def test_load_rejects_duplicate_mapping_key() -> None:
    with pytest.raises(_Error, match=r"workflow YAML duplicates mapping key 'jobs'"):
        _load("jobs: {}\njobs: {}\n")


def test_load_rejects_unhashable_mapping_key() -> None:
    with pytest.raises(_Error, match="workflow YAML mapping keys must be hashable"):
        _load("? [unhashable]\n: true\n")


def test_load_rejects_malformed_yaml() -> None:
    with pytest.raises(_Error, match="cannot parse workflow YAML"):
        _load("jobs: [\n")


def test_load_rejects_non_mapping_document() -> None:
    with pytest.raises(_Error, match="workflow must be an object"):
        _load("[]\n")


def test_contains_mapping_key_matches_top_level() -> None:
    assert contains_mapping_key({"continue-on-error": True}, "continue-on-error") is True


def test_contains_mapping_key_matches_nested_mapping() -> None:
    document = {"steps": {"inner": {"continue-on-error": False}}}
    assert contains_mapping_key(document, "continue-on-error") is True


def test_contains_mapping_key_matches_within_list() -> None:
    document = {"steps": [{"run": "x"}, {"continue-on-error": True}]}
    assert contains_mapping_key(document, "continue-on-error") is True


def test_contains_mapping_key_absent_returns_false() -> None:
    assert contains_mapping_key({"steps": [{"run": "x"}]}, "continue-on-error") is False


def test_contains_mapping_key_scalar_returns_false() -> None:
    assert contains_mapping_key("continue-on-error", "continue-on-error") is False
