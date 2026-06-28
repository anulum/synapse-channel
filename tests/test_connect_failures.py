# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for hub connection-failure classification

from __future__ import annotations

from synapse_channel.connect_failures import (
    CAPACITY_CLOSE_CODE,
    NAME_CONFLICT_CLOSE_CODE,
    SUPERSEDED_CLOSE_CODE,
    TAKEOVER_COOLDOWN_CLOSE_CODE,
    describe_connect_failure,
    explain_silent_outcome,
)


def test_absent_hub_keeps_the_generic_unreachable_line() -> None:
    message = describe_connect_failure("A", "ws://localhost:8876", close_code=None)

    assert message == "[A] Could not reach hub at ws://localhost:8876."


def test_capacity_close_explains_the_cap_and_remedy() -> None:
    message = describe_connect_failure(
        "A", "ws://localhost:8876", close_code=CAPACITY_CLOSE_CODE, close_reason="hub at capacity"
    )

    assert "hub at capacity" in message
    assert "--max-clients" in message
    assert f"code {CAPACITY_CLOSE_CODE}" in message
    # The reason matches the guidance, so it is not duplicated as a suffix.
    assert "hub said" not in message


def test_name_conflict_close_directs_to_a_unique_name() -> None:
    message = describe_connect_failure(
        "A/dup", "ws://localhost:8876", close_code=NAME_CONFLICT_CLOSE_CODE
    )

    assert "unique --name" in message
    assert f"code {NAME_CONFLICT_CLOSE_CODE}" in message


def test_superseded_and_cooldown_codes_are_recognised() -> None:
    superseded = describe_connect_failure("A", "ws://h", close_code=SUPERSEDED_CLOSE_CODE)
    cooldown = describe_connect_failure("A", "ws://h", close_code=TAKEOVER_COOLDOWN_CLOSE_CODE)

    assert "superseded" in superseded
    assert "cooldown" in cooldown


def test_unknown_close_code_reports_code_and_reason() -> None:
    message = describe_connect_failure(
        "A", "ws://h", close_code=4099, close_reason="experimental drain"
    )

    assert "code 4099" in message
    assert "experimental drain" in message


def test_unknown_close_code_without_reason_omits_the_detail_suffix() -> None:
    message = describe_connect_failure("A", "ws://h", close_code=4099)

    assert message == "[A] Hub closed the connection (code 4099)."


def test_recognised_code_appends_a_distinct_hub_reason() -> None:
    message = describe_connect_failure(
        "A",
        "ws://h",
        close_code=CAPACITY_CLOSE_CODE,
        close_reason="evicted: maintenance window",
    )

    assert "hub said: evicted: maintenance window" in message


def test_silent_outcome_keeps_the_fallback_when_socket_stayed_open() -> None:
    message = explain_silent_outcome(
        "A", "ws://h", close_code=None, close_reason="", fallback="no response from hub"
    )

    assert message == "no response from hub"


def test_silent_outcome_surfaces_the_close_code_when_present() -> None:
    message = explain_silent_outcome(
        "A",
        "ws://h",
        close_code=NAME_CONFLICT_CLOSE_CODE,
        close_reason="name conflict",
        fallback="no response from hub",
    )

    assert "already online" in message
    assert "code 4009" in message
    assert message != "no response from hub"
