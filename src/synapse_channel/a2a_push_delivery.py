# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — typed A2A push-delivery outcomes and retry policy
"""Typed, credential-free evidence for outbound A2A push delivery."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from urllib.error import URLError

from synapse_channel.a2a import JsonMap

DEFAULT_PUSH_RETRY_DELAYS_SECONDS = (0.25, 1.0)
"""Delays before the second and third bounded delivery attempts."""

MAX_PUSH_RETRY_COUNT = 7
"""Hard ceiling on retries accepted by the in-process policy."""

MAX_PUSH_RETRY_WINDOW_SECONDS = 60.0
"""Hard ceiling on the sum of configured retry delays."""


class PushDeliveryState(str, Enum):
    """Stable terminal and intermediate push-delivery states."""

    RETRY_SCHEDULED = "retry-scheduled"
    SUCCEEDED = "succeeded"
    DEAD_LETTERED = "dead-lettered"


class PushFailureClass(str, Enum):
    """Credential-free failure classes safe to persist and expose."""

    TIMEOUT = "timeout"
    URL_ERROR = "url-error"
    OS_ERROR = "os-error"


@dataclass(frozen=True, slots=True)
class PushDeliveryAttempt:
    """One sanitized push-delivery attempt suitable for durable storage."""

    task_id: str
    config_id: str
    delivery_id: str
    attempt: int
    state: PushDeliveryState
    occurred_at: float
    retry_window_seconds: float
    failure_class: PushFailureClass | None = None
    retry_delay_seconds: float | None = None
    retry_at: float | None = None

    def to_json(self) -> JsonMap:
        """Return the public credential-free evidence representation."""
        payload: JsonMap = {
            "taskId": self.task_id,
            "configId": self.config_id,
            "deliveryId": self.delivery_id,
            "attempt": self.attempt,
            "state": self.state.value,
            "occurredAt": self.occurred_at,
            "retryWindowSeconds": self.retry_window_seconds,
        }
        if self.failure_class is not None:
            payload["failureClass"] = self.failure_class.value
        if self.retry_delay_seconds is not None:
            payload["retryDelaySeconds"] = self.retry_delay_seconds
        if self.retry_at is not None:
            payload["retryAt"] = self.retry_at
        return payload


@dataclass(frozen=True, slots=True)
class PushDeliveryResult:
    """Typed final result for one task/config push-delivery run."""

    task_id: str
    config_id: str
    delivery_id: str
    state: PushDeliveryState
    attempts: int
    completed_at: float
    retry_window_seconds: float
    failure_class: PushFailureClass | None = None

    def to_json(self) -> JsonMap:
        """Return the public final-result representation."""
        payload: JsonMap = {
            "taskId": self.task_id,
            "configId": self.config_id,
            "deliveryId": self.delivery_id,
            "state": self.state.value,
            "attempts": self.attempts,
            "completedAt": self.completed_at,
            "retryWindowSeconds": self.retry_window_seconds,
        }
        if self.failure_class is not None:
            payload["failureClass"] = self.failure_class.value
        return payload


AttemptRecorder = Callable[[PushDeliveryAttempt], None]
Sleeper = Callable[[float], None]
Clock = Callable[[], float]


def validate_retry_delays(delays: Sequence[float]) -> tuple[float, ...]:
    """Return a bounded finite retry schedule or raise ``ValueError``."""
    normalized = tuple(float(delay) for delay in delays)
    if len(normalized) > MAX_PUSH_RETRY_COUNT:
        raise ValueError(f"push retry policy allows at most {MAX_PUSH_RETRY_COUNT} retries")
    if any(not math.isfinite(delay) or delay < 0.0 for delay in normalized):
        raise ValueError("push retry delays must be finite and non-negative")
    if sum(normalized) > MAX_PUSH_RETRY_WINDOW_SECONDS:
        raise ValueError(
            f"push retry window must not exceed {MAX_PUSH_RETRY_WINDOW_SECONDS:g} seconds"
        )
    return normalized


def failure_class(exc: OSError | TimeoutError | URLError) -> PushFailureClass:
    """Reduce a transport exception to a stable value-free failure class."""
    if isinstance(exc, TimeoutError):
        return PushFailureClass.TIMEOUT
    if isinstance(exc, URLError):
        return PushFailureClass.URL_ERROR
    return PushFailureClass.OS_ERROR


def deliver_with_retries(
    *,
    task_id: str,
    config_id: str,
    delivery_id: str,
    deliver: Callable[[], None],
    record_attempt: AttemptRecorder,
    retry_delays_seconds: Sequence[float] = DEFAULT_PUSH_RETRY_DELAYS_SECONDS,
    sleep: Sleeper = time.sleep,
    clock: Clock = time.time,
) -> PushDeliveryResult:
    """Run bounded delivery attempts and durably report each sanitized outcome.

    Only expected network failures enter the retry policy. Programming errors
    propagate instead of being mislabeled as delivery failures. The recorder is
    called synchronously after every attempt, so a configured persistent store
    commits evidence before the next retry begins or the caller receives the
    final result.
    """
    delays = validate_retry_delays(retry_delays_seconds)
    retry_window = sum(delays)
    index = 0
    while True:
        attempt_number = index + 1
        try:
            deliver()
        except (OSError, TimeoutError, URLError) as exc:
            failed_at = clock()
            classified = failure_class(exc)
            if index < len(delays):
                delay = delays[index]
                record_attempt(
                    PushDeliveryAttempt(
                        task_id=task_id,
                        config_id=config_id,
                        delivery_id=delivery_id,
                        attempt=attempt_number,
                        state=PushDeliveryState.RETRY_SCHEDULED,
                        occurred_at=failed_at,
                        retry_window_seconds=retry_window,
                        failure_class=classified,
                        retry_delay_seconds=delay,
                        retry_at=failed_at + delay,
                    )
                )
                sleep(delay)
                index += 1
                continue
            record_attempt(
                PushDeliveryAttempt(
                    task_id=task_id,
                    config_id=config_id,
                    delivery_id=delivery_id,
                    attempt=attempt_number,
                    state=PushDeliveryState.DEAD_LETTERED,
                    occurred_at=failed_at,
                    retry_window_seconds=retry_window,
                    failure_class=classified,
                )
            )
            return PushDeliveryResult(
                task_id=task_id,
                config_id=config_id,
                delivery_id=delivery_id,
                state=PushDeliveryState.DEAD_LETTERED,
                attempts=attempt_number,
                completed_at=failed_at,
                retry_window_seconds=retry_window,
                failure_class=classified,
            )
        succeeded_at = clock()
        record_attempt(
            PushDeliveryAttempt(
                task_id=task_id,
                config_id=config_id,
                delivery_id=delivery_id,
                attempt=attempt_number,
                state=PushDeliveryState.SUCCEEDED,
                occurred_at=succeeded_at,
                retry_window_seconds=retry_window,
            )
        )
        return PushDeliveryResult(
            task_id=task_id,
            config_id=config_id,
            delivery_id=delivery_id,
            state=PushDeliveryState.SUCCEEDED,
            attempts=attempt_number,
            completed_at=succeeded_at,
            retry_window_seconds=retry_window,
        )
