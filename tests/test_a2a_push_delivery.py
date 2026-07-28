# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — typed and durable A2A push-delivery outcome tests

from __future__ import annotations

from collections.abc import Callable, Iterator
from urllib.error import URLError

import pytest

from synapse_channel.a2a_push import deliver_push_notification
from synapse_channel.a2a_push_delivery import (
    MAX_PUSH_RETRY_COUNT,
    PushDeliveryAttempt,
    PushDeliveryResult,
    PushDeliveryState,
    PushFailureClass,
    deliver_with_retries,
    failure_class,
    validate_retry_delays,
)


def _clock(values: Iterator[float]) -> Callable[[], float]:
    """Return a deterministic wall clock backed by ``values``."""
    return lambda: next(values)


def test_attempt_and_result_json_omit_absent_sensitive_fields() -> None:
    """Public evidence contains policy facts and no delivery payload or URL."""
    succeeded = PushDeliveryAttempt(
        task_id="task-a",
        config_id="cfg-a",
        delivery_id="delivery-a",
        attempt=1,
        state=PushDeliveryState.SUCCEEDED,
        occurred_at=10.0,
        retry_window_seconds=1.25,
    )
    retry = PushDeliveryAttempt(
        task_id="task-a",
        config_id="cfg-a",
        delivery_id="delivery-a",
        attempt=1,
        state=PushDeliveryState.RETRY_SCHEDULED,
        occurred_at=11.0,
        retry_window_seconds=1.25,
        failure_class=PushFailureClass.URL_ERROR,
        retry_delay_seconds=0.25,
        retry_at=11.25,
    )
    dead = PushDeliveryResult(
        task_id="task-a",
        config_id="cfg-a",
        delivery_id="delivery-a",
        state=PushDeliveryState.DEAD_LETTERED,
        attempts=3,
        completed_at=12.0,
        retry_window_seconds=1.25,
        failure_class=PushFailureClass.TIMEOUT,
    )
    result = PushDeliveryResult(
        task_id="task-a",
        config_id="cfg-a",
        delivery_id="delivery-a",
        state=PushDeliveryState.SUCCEEDED,
        attempts=1,
        completed_at=10.0,
        retry_window_seconds=1.25,
    )

    assert succeeded.to_json() == {
        "taskId": "task-a",
        "configId": "cfg-a",
        "deliveryId": "delivery-a",
        "attempt": 1,
        "state": "succeeded",
        "occurredAt": 10.0,
        "retryWindowSeconds": 1.25,
    }
    assert retry.to_json()["retryAt"] == 11.25
    assert dead.to_json()["failureClass"] == "timeout"
    assert "failureClass" not in result.to_json()
    assert all(key not in retry.to_json() for key in ("url", "headers", "payload", "error"))


@pytest.mark.parametrize(
    ("delays", "message"),
    [
        ((1.0,) * (MAX_PUSH_RETRY_COUNT + 1), "at most"),
        ((-1.0,), "finite and non-negative"),
        ((float("inf"),), "finite and non-negative"),
        ((61.0,), "must not exceed"),
    ],
)
def test_retry_schedule_validation_fails_closed(delays: tuple[float, ...], message: str) -> None:
    """Unbounded or non-finite retry policies never reach delivery."""
    with pytest.raises(ValueError, match=message):
        validate_retry_delays(delays)


def test_retry_schedule_validation_normalizes_numbers() -> None:
    """Accepted delays have one stable floating-point representation."""
    assert validate_retry_delays((0, 1)) == (0.0, 1.0)


def test_failure_classes_are_stable_and_value_free() -> None:
    """Transport exception messages cannot enter the persisted classification."""
    assert failure_class(TimeoutError("secret")) is PushFailureClass.TIMEOUT
    assert failure_class(URLError("secret")) is PushFailureClass.URL_ERROR
    assert failure_class(OSError("secret")) is PushFailureClass.OS_ERROR


def test_delivery_succeeds_on_first_attempt() -> None:
    """A first-attempt success records one terminal success and never sleeps."""
    attempts: list[PushDeliveryAttempt] = []
    sleeps: list[float] = []

    result = deliver_with_retries(
        task_id="task-a",
        config_id="cfg-a",
        delivery_id="delivery-a",
        deliver=lambda: None,
        record_attempt=attempts.append,
        retry_delays_seconds=(0.25, 1.0),
        sleep=sleeps.append,
        clock=lambda: 10.0,
    )

    assert result.state is PushDeliveryState.SUCCEEDED
    assert result.attempts == 1
    assert attempts == [
        PushDeliveryAttempt(
            task_id="task-a",
            config_id="cfg-a",
            delivery_id="delivery-a",
            attempt=1,
            state=PushDeliveryState.SUCCEEDED,
            occurred_at=10.0,
            retry_window_seconds=1.25,
        )
    ]
    assert sleeps == []


def test_delivery_retries_then_succeeds() -> None:
    """A recoverable failure records its schedule before the successful retry."""
    calls = 0
    attempts: list[PushDeliveryAttempt] = []
    sleeps: list[float] = []

    def deliver() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise URLError("credential-bearing diagnostic")

    result = deliver_with_retries(
        task_id="task-a",
        config_id="cfg-a",
        delivery_id="delivery-a",
        deliver=deliver,
        record_attempt=attempts.append,
        retry_delays_seconds=(2.0,),
        sleep=sleeps.append,
        clock=_clock(iter((100.0, 102.0))),
    )

    assert result.to_json() == {
        "taskId": "task-a",
        "configId": "cfg-a",
        "deliveryId": "delivery-a",
        "state": "succeeded",
        "attempts": 2,
        "completedAt": 102.0,
        "retryWindowSeconds": 2.0,
    }
    assert [attempt.state for attempt in attempts] == [
        PushDeliveryState.RETRY_SCHEDULED,
        PushDeliveryState.SUCCEEDED,
    ]
    assert attempts[0].retry_at == 102.0
    assert sleeps == [2.0]


def test_delivery_exhaustion_is_terminal_dead_letter() -> None:
    """Exhausting the bounded policy records one terminal dead-letter result."""
    attempts: list[PushDeliveryAttempt] = []
    sleeps: list[float] = []

    def fail() -> None:
        raise TimeoutError("credential-bearing diagnostic")

    result = deliver_with_retries(
        task_id="task-a",
        config_id="cfg-a",
        delivery_id="delivery-a",
        deliver=fail,
        record_attempt=attempts.append,
        retry_delays_seconds=(0.0, 1.0),
        sleep=sleeps.append,
        clock=_clock(iter((1.0, 2.0, 3.0))),
    )

    assert result.state is PushDeliveryState.DEAD_LETTERED
    assert result.failure_class is PushFailureClass.TIMEOUT
    assert result.attempts == 3
    assert [attempt.state for attempt in attempts] == [
        PushDeliveryState.RETRY_SCHEDULED,
        PushDeliveryState.RETRY_SCHEDULED,
        PushDeliveryState.DEAD_LETTERED,
    ]
    assert sleeps == [0.0, 1.0]


def test_unexpected_delivery_bug_propagates_without_false_evidence() -> None:
    """Programming failures are not mislabeled as remote delivery outcomes."""
    attempts: list[PushDeliveryAttempt] = []

    def fail() -> None:
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError, match="bug"):
        deliver_with_retries(
            task_id="task-a",
            config_id="cfg-a",
            delivery_id="delivery-a",
            deliver=fail,
            record_attempt=attempts.append,
            retry_delays_seconds=(),
        )

    assert attempts == []


def test_public_delivery_wrapper_returns_typed_result_without_recorder() -> None:
    """Callers without a store still receive the typed final result."""
    deliveries: list[dict[str, object]] = []

    result = deliver_push_notification(
        task={"id": "task-a"},
        config={"id": "cfg-a", "webhookUrl": "https://example.test/hook"},
        push_deliverer=deliveries.append,
        delivery_id="delivery-a",
        retry_delays_seconds=(),
        clock=lambda: 10.0,
    )

    assert result.to_json() == {
        "taskId": "task-a",
        "configId": "cfg-a",
        "deliveryId": "delivery-a",
        "state": "succeeded",
        "attempts": 1,
        "completedAt": 10.0,
        "retryWindowSeconds": 0,
    }
    assert len(deliveries) == 1
