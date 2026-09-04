# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — package-owned setup profile tests

from __future__ import annotations

import pytest

from synapse_channel.setup_contract import canonical_json, setup_schema
from synapse_channel.setup_profiles import (
    available_setup_profiles,
    build_setup_spec,
    get_setup_profile,
)


def test_initial_profile_is_explicit_and_unknown_profiles_are_rejected() -> None:
    assert available_setup_profiles() == ("local-single-user",)
    assert get_setup_profile("unknown") is None


def test_spec_is_deterministic_complete_and_read_only() -> None:
    profile = get_setup_profile("local-single-user")
    assert profile is not None

    first = build_setup_spec(profile)
    second = build_setup_spec(profile)
    assert canonical_json(first) == canonical_json(second)
    assert first["supported_operations"] == [
        "spec",
        "inspect",
        "plan",
        "authorize",
        "apply",
        "verification-plan",
        "authorize-verification",
        "verify",
    ]
    assert first["read_only"] is True
    requirements = first["requirements"]
    assert isinstance(requirements, list)
    assert {item["id"] for item in requirements} == {
        "package",
        "python",
        "platform",
        "executable",
        "identity",
        "hub",
        "waiter",
        "service_manager",
    }


def test_spec_validates_against_the_packaged_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    profile = get_setup_profile("local-single-user")
    assert profile is not None
    jsonschema.validate(build_setup_spec(profile), setup_schema())
