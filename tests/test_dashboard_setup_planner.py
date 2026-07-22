# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded setup doctor and one-use plan-store tests
"""Prove F9 planning remains bounded, expiring, single-use, and effect-free."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import cast
from uuid import uuid4

import pytest

from synapse_channel.dashboard_setup_contract import SetupApplyRequest, SetupPlanRequest
from synapse_channel.dashboard_setup_planner import (
    MAX_SETUP_PLAN_CAPACITY,
    MAX_SETUP_PLAN_TTL_SECONDS,
    AuthorisedSetupPlan,
    IssuedSetupPlan,
    SetupDoctorFacts,
    SetupPlanBinding,
    SetupPlanStore,
    SetupPlanStoreError,
    build_setup_preflight,
)

REQUEST_ID = "4dc282e6-21eb-4f5c-89d8-ea824836ef65"
PLAN_ID = "plan_id_0123456789abcdef"
NONCE = "nonce_0123456789abcdef"
GENERATION = "a" * 64
SALT = b"s" * 32


def binding(**overrides: str) -> SetupPlanBinding:
    values = {
        "principal_id": "setup-admin",
        "host": "127.0.0.1:8765",
        "origin": "http://127.0.0.1:8765",
        "configuration_generation": GENERATION,
        **overrides,
    }
    return SetupPlanBinding(
        principal_id=values["principal_id"],
        host=values["host"],
        origin=values["origin"],
        configuration_generation=values["configuration_generation"],
    )


def token_factory(*values: str) -> Callable[[int], str]:
    tokens = iter(values)

    def next_token(_bytes: int) -> str:
        return next(tokens)

    return next_token


def salt_factory(_bytes: int) -> bytes:
    return SALT


def store_with_tokens(
    *tokens: str,
    capacity: int = 128,
    ttl_seconds: int = 180,
) -> SetupPlanStore:
    return SetupPlanStore(
        capacity=capacity,
        ttl_seconds=ttl_seconds,
        token_factory=token_factory(*tokens),
        salt_factory=salt_factory,
    )


def issue(
    store: SetupPlanStore,
    *,
    request_id: str = REQUEST_ID,
    context: SetupPlanBinding | None = None,
    now: int = 100,
) -> IssuedSetupPlan:
    result = store.issue(
        SetupPlanRequest(request_id, "local-ephemeral"),
        binding=context or binding(),
        now=now,
    )
    assert isinstance(result, IssuedSetupPlan)
    return result


def apply_request(plan: IssuedSetupPlan, **overrides: str) -> SetupApplyRequest:
    values = {
        "request_id": plan.plan.request_id,
        "plan_id": plan.plan_id,
        "plan_digest": plan.plan.digest,
        "confirmation_nonce": plan.confirmation_nonce,
        **overrides,
    }
    return SetupApplyRequest(
        request_id=values["request_id"],
        plan_id=values["plan_id"],
        plan_digest=values["plan_digest"],
        confirmation_nonce=values["confirmation_nonce"],
    )


def error_code(result: object) -> str:
    assert isinstance(result, SetupPlanStoreError)
    return result.code


def test_preflight_is_versioned_bounded_and_token_free() -> None:
    preflight = build_setup_preflight(
        SetupDoctorFacts(
            apply_armed=True,
            loopback=True,
            runtime="ready",
            user_services="absent",
            receipt_store="blocked",
        )
    )

    assert preflight.as_dict() == {
        "version": 1,
        "apply_armed": True,
        "loopback": True,
        "runtime": "ready",
        "user_services": "absent",
        "receipt_store": "blocked",
        "profiles": ["local-ephemeral", "local-durable-existing"],
        "limits": {
            "creates_secrets": False,
            "broader_bind": False,
            "system_services": False,
        },
    }
    assert "token" not in repr(preflight.as_dict()).lower()
    assert "/home/" not in repr(preflight.as_dict())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("principal_id", "", "principal"),
        ("principal_id", "x" * 129, "principal"),
        ("host", "bad\nhost", "host"),
        ("host", "ž", "host"),
        ("origin", "x" * 513, "origin"),
    ],
)
def test_binding_rejects_unbounded_or_ambiguous_context(
    field: str,
    value: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        binding(**{field: value})


@pytest.mark.parametrize("generation", ["", "A" * 64, "g" * 64])
def test_binding_requires_a_lowercase_configuration_digest(generation: str) -> None:
    with pytest.raises(ValueError, match="configuration generation"):
        binding(configuration_generation=generation)


@pytest.mark.parametrize("capacity", [True, 0, MAX_SETUP_PLAN_CAPACITY + 1, "1"])
def test_store_rejects_invalid_capacity(capacity: object) -> None:
    with pytest.raises(ValueError, match="capacity"):
        SetupPlanStore(capacity=cast(int, capacity))


@pytest.mark.parametrize("ttl", [True, 0, MAX_SETUP_PLAN_TTL_SECONDS + 1, "1"])
def test_store_rejects_invalid_ttl(ttl: object) -> None:
    with pytest.raises(ValueError, match="TTL"):
        SetupPlanStore(ttl_seconds=cast(int, ttl))


def test_issue_builds_a_deterministic_context_bound_plan() -> None:
    store = store_with_tokens(PLAN_ID, NONCE)
    plan = issue(store)

    assert plan.plan_id == PLAN_ID
    assert plan.confirmation_nonce == NONCE
    assert plan.plan.expires_at == 280
    assert plan.plan.configuration_generation == GENERATION
    assert plan.as_dict()["mutates_local_state"] is True
    assert plan.as_dict()["plan_digest"] == plan.plan.digest
    assert store.record_count == 1
    assert NONCE not in repr(store)


@pytest.mark.parametrize(
    "tokens",
    [
        ("short", NONCE),
        (PLAN_ID, "spaces are invalid here"),
    ],
)
def test_issue_rejects_invalid_generated_tokens(tokens: tuple[str, str]) -> None:
    store = store_with_tokens(*tokens)
    assert (
        error_code(
            store.issue(SetupPlanRequest(REQUEST_ID, "local-ephemeral"), binding=binding(), now=1)
        )
        == "invalid_token"
    )
    assert store.record_count == 0


def test_issue_rejects_an_invalid_salt_factory() -> None:
    store = SetupPlanStore(
        token_factory=token_factory(PLAN_ID, NONCE),
        salt_factory=lambda _size: b"short",
    )

    result = store.issue(SetupPlanRequest(REQUEST_ID, "local-ephemeral"), binding=binding(), now=1)

    assert error_code(result) == "invalid_token"
    assert store.record_count == 0


def test_duplicate_request_is_refused_without_replacing_the_first_plan() -> None:
    store = store_with_tokens(PLAN_ID, NONCE)
    issue(store)

    result = store.issue(
        SetupPlanRequest(REQUEST_ID, "local-durable-existing"),
        binding=binding(principal_id="another-admin"),
        now=101,
    )

    assert error_code(result) == "duplicate_request"
    assert store.record_count == 1


def test_capacity_refuses_without_evicting_an_unexpired_plan() -> None:
    store = store_with_tokens(PLAN_ID, NONCE, capacity=1)
    first = issue(store)

    result = store.issue(
        SetupPlanRequest(str(uuid4()), "local-ephemeral"), binding=binding(), now=101
    )

    assert error_code(result) == "capacity"
    assert store.record_count == 1
    authorised = store.authorise_once(apply_request(first), binding=binding(), now=102)
    assert isinstance(authorised, AuthorisedSetupPlan)


def test_generated_plan_id_collision_is_refused() -> None:
    store = store_with_tokens(PLAN_ID, NONCE, PLAN_ID, "second_nonce_0123456789")
    issue(store)

    result = store.issue(
        SetupPlanRequest(str(uuid4()), "local-ephemeral"), binding=binding(), now=101
    )

    assert error_code(result) == "token_collision"
    assert store.record_count == 1


def test_expiry_frees_capacity_before_a_new_issue() -> None:
    second_plan_id = "second_plan_0123456789ab"
    second_nonce = "second_nonce_0123456789a"
    store = store_with_tokens(
        PLAN_ID,
        NONCE,
        second_plan_id,
        second_nonce,
        capacity=1,
        ttl_seconds=2,
    )
    issue(store, now=10)

    second = issue(store, request_id=str(uuid4()), now=12)

    assert second.plan_id == second_plan_id
    assert store.record_count == 1


def test_purge_expired_reports_exact_removed_count() -> None:
    store = store_with_tokens(
        PLAN_ID,
        NONCE,
        "second_plan_0123456789ab",
        "second_nonce_0123456789a",
        ttl_seconds=2,
    )
    issue(store, now=10)
    issue(store, request_id=str(uuid4()), now=11)

    assert store.purge_expired(now=12) == 1
    assert store.record_count == 1
    assert store.purge_expired(now=13) == 1
    assert store.purge_expired(now=14) == 0


def test_authorise_consumes_a_matching_plan_exactly_once() -> None:
    store = store_with_tokens(PLAN_ID, NONCE)
    plan = issue(store)

    first = store.authorise_once(apply_request(plan), binding=binding(), now=101)
    second = store.authorise_once(apply_request(plan), binding=binding(), now=101)

    assert first == AuthorisedSetupPlan(PLAN_ID, plan.plan)
    assert error_code(second) == "replayed"
    assert store.record_count == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_id", "00000000-0000-4000-8000-000000000000"),
        ("plan_digest", "b" * 64),
        ("confirmation_nonce", "another_nonce_0123456789"),
    ],
)
def test_authorise_refuses_every_mismatched_apply_value(field: str, value: str) -> None:
    store = store_with_tokens(PLAN_ID, NONCE)
    plan = issue(store)

    result = store.authorise_once(apply_request(plan, **{field: value}), binding=binding(), now=101)

    assert error_code(result) == "mismatch"
    assert isinstance(
        store.authorise_once(apply_request(plan), binding=binding(), now=101),
        AuthorisedSetupPlan,
    )


@pytest.mark.parametrize(
    "context",
    [
        binding(principal_id="other-admin"),
        binding(host="localhost:8765"),
        binding(origin="http://localhost:8765"),
        binding(configuration_generation="b" * 64),
    ],
)
def test_authorise_refuses_a_different_request_context(context: SetupPlanBinding) -> None:
    store = store_with_tokens(PLAN_ID, NONCE)
    plan = issue(store)

    assert (
        error_code(store.authorise_once(apply_request(plan), binding=context, now=101))
        == "mismatch"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("plan_id", "short"),
        ("plan_digest", "not-a-digest"),
        ("confirmation_nonce", "ž" * 24),
    ],
)
def test_authorise_rejects_malformed_direct_dataclass_calls(field: str, value: str) -> None:
    store = store_with_tokens(PLAN_ID, NONCE)
    plan = issue(store)

    assert (
        error_code(
            store.authorise_once(apply_request(plan, **{field: value}), binding=binding(), now=101)
        )
        == "mismatch"
    )


def test_unknown_and_expired_plans_have_distinct_bounded_outcomes() -> None:
    store = store_with_tokens(PLAN_ID, NONCE, ttl_seconds=2)
    plan = issue(store, now=10)
    unknown = replace(apply_request(plan), plan_id="unknown_plan_0123456789ab")

    assert error_code(store.authorise_once(unknown, binding=binding(), now=11)) == "not_found"
    assert (
        error_code(store.authorise_once(apply_request(plan), binding=binding(), now=12))
        == "expired"
    )
    assert store.record_count == 0


@pytest.mark.parametrize("now", [True, -1, 1.5, "1"])
def test_store_rejects_invalid_time_across_public_operations(now: object) -> None:
    store = store_with_tokens(PLAN_ID, NONCE)
    with pytest.raises(ValueError, match="time"):
        store.issue(
            SetupPlanRequest(REQUEST_ID, "local-ephemeral"),
            binding=binding(),
            now=cast(int, now),
        )
    with pytest.raises(ValueError, match="time"):
        store.purge_expired(now=cast(int, now))


def test_concurrent_confirmation_authorises_exactly_once() -> None:
    store = store_with_tokens(PLAN_ID, NONCE)
    plan = issue(store)
    request = apply_request(plan)

    def authorise(_attempt: int) -> AuthorisedSetupPlan | SetupPlanStoreError:
        return store.authorise_once(request, binding=binding(), now=101)

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(authorise, range(32)))

    assert sum(isinstance(outcome, AuthorisedSetupPlan) for outcome in outcomes) == 1
    assert [outcome.code for outcome in outcomes if isinstance(outcome, SetupPlanStoreError)] == [
        "replayed"
    ] * 31
