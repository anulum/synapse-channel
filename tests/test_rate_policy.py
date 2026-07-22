# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — auto-enable rate-policy decision regressions (REV-SEC-06)

from __future__ import annotations

import math

import pytest

from synapse_channel.core.hub_config import HubAuthConfig
from synapse_channel.core.rate_policy import (
    HubExposurePosture,
    RateLimits,
    decide_auto_rate_policy,
    is_loopback_bind,
)
from synapse_channel.core.secure import (
    SECURE_AGENT_BURST,
    SECURE_AGENT_RATE,
    SECURE_HOST_BURST,
    SECURE_HOST_RATE,
    SECURE_MAX_CONNECTIONS_PER_HOST,
)

DISABLED = RateLimits()


class TestIsLoopbackBind:
    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "LOCALHOST",
            " localhost ",
            "127.0.0.1",
            "127.1.2.3",
            "::1",
            "::ffff:127.0.0.1",
        ],
    )
    def test_recognised_loopback_forms_are_loopback(self, host: str) -> None:
        assert is_loopback_bind(host) is True

    @pytest.mark.parametrize(
        "host",
        [
            "0.0.0.0",
            "::",
            "",
            "   ",
            "192.168.1.10",
            "10.0.0.5",
            "example.com",
            "hub.local",
            "128.0.0.1",
        ],
    )
    def test_exposed_or_unknown_binds_are_not_loopback(self, host: str) -> None:
        assert is_loopback_bind(host) is False

    def test_none_host_is_not_loopback(self) -> None:
        assert is_loopback_bind(None) is False

    def test_malformed_127_prefix_fails_safe_to_exposed(self) -> None:
        # "127.foo" must not be treated as loopback just because of the prefix.
        assert is_loopback_bind("127.foo") is False

    def test_out_of_range_octet_is_not_loopback(self) -> None:
        assert is_loopback_bind("127.0.0.256") is False


class TestHubExposurePosture:
    def test_default_posture_is_local_first(self) -> None:
        posture = HubExposurePosture()
        assert posture.triggers == ()
        assert posture.is_exposed is False

    def test_triggers_report_every_active_signal_in_stable_order(self) -> None:
        posture = HubExposurePosture(
            off_loopback_bind=True,
            token_configured=True,
            bridge_exposed=True,
            multi_seat=True,
        )
        assert posture.triggers == (
            "off-loopback bind",
            "connect token configured",
            "A2A/MCP bridge exposed",
            "multi-seat",
        )
        assert posture.is_exposed is True

    @pytest.mark.parametrize(
        "field",
        ["off_loopback_bind", "token_configured", "bridge_exposed", "multi_seat"],
    )
    def test_any_single_signal_marks_the_posture_exposed(self, field: str) -> None:
        posture = HubExposurePosture(**{field: True})
        assert posture.is_exposed is True
        assert len(posture.triggers) == 1


class TestDecideAutoRatePolicy:
    def test_local_first_posture_enables_nothing(self) -> None:
        decision = decide_auto_rate_policy(HubExposurePosture(), DISABLED)
        assert decision.auto_enabled is False
        assert decision.filled == ()
        assert decision.limits == DISABLED
        assert "local-first" in decision.report_lines[0]

    def test_secure_mode_defers_and_fills_nothing(self) -> None:
        decision = decide_auto_rate_policy(
            HubExposurePosture(off_loopback_bind=True), DISABLED, secure_mode=True
        )
        assert decision.auto_enabled is False
        assert decision.filled == ()
        assert decision.limits == DISABLED
        assert "deferred to --secure" in decision.report_lines[0]

    def test_exposed_posture_fills_every_disabled_limit_with_secure_defaults(self) -> None:
        decision = decide_auto_rate_policy(HubExposurePosture(off_loopback_bind=True), DISABLED)
        assert decision.auto_enabled is True
        assert decision.filled == ("per-agent rate", "per-host rate", "per-host connection cap")
        assert decision.limits == RateLimits(
            agent_rate=SECURE_AGENT_RATE,
            agent_burst=SECURE_AGENT_BURST,
            host_rate=SECURE_HOST_RATE,
            host_burst=SECURE_HOST_BURST,
            max_connections_per_host=SECURE_MAX_CONNECTIONS_PER_HOST,
        )

    @pytest.mark.parametrize(
        "posture",
        [
            HubExposurePosture(token_configured=True),
            HubExposurePosture(bridge_exposed=True),
            HubExposurePosture(multi_seat=True),
        ],
    )
    def test_any_exposure_signal_triggers_auto_enable(self, posture: HubExposurePosture) -> None:
        decision = decide_auto_rate_policy(posture, DISABLED)
        assert decision.auto_enabled is True
        assert decision.limits.agent_rate == SECURE_AGENT_RATE

    def test_operator_stricter_limit_is_preserved(self) -> None:
        operator = RateLimits(agent_rate=50.0, agent_burst=5.0)
        decision = decide_auto_rate_policy(HubExposurePosture(off_loopback_bind=True), operator)
        assert decision.limits.agent_rate == 50.0
        assert decision.limits.agent_burst == 5.0
        assert "per-agent rate" not in decision.filled
        # The disabled host/connection limits are still filled.
        assert "per-host rate" in decision.filled
        assert "per-host connection cap" in decision.filled

    def test_operator_looser_limit_is_not_clamped(self) -> None:
        # Auto-enable fills gaps; clamping a loose value is --secure's job.
        operator = RateLimits(agent_rate=SECURE_AGENT_RATE * 4)
        decision = decide_auto_rate_policy(HubExposurePosture(off_loopback_bind=True), operator)
        assert decision.limits.agent_rate == SECURE_AGENT_RATE * 4
        assert "per-agent rate" not in decision.filled

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, -1.0, 0.0])
    def test_disabled_or_non_finite_rate_is_filled_in_exposed_posture(self, bad: float) -> None:
        operator = RateLimits(agent_rate=bad, host_rate=bad)
        decision = decide_auto_rate_policy(HubExposurePosture(bridge_exposed=True), operator)
        assert decision.limits.agent_rate == SECURE_AGENT_RATE
        assert decision.limits.host_rate == SECURE_HOST_RATE
        assert "per-agent rate" in decision.filled
        assert "per-host rate" in decision.filled

    def test_operator_connection_cap_is_preserved(self) -> None:
        operator = RateLimits(max_connections_per_host=3)
        decision = decide_auto_rate_policy(HubExposurePosture(off_loopback_bind=True), operator)
        assert decision.limits.max_connections_per_host == 3
        assert "per-host connection cap" not in decision.filled

    def test_all_limits_already_set_reports_no_fill(self) -> None:
        operator = RateLimits(
            agent_rate=10.0,
            agent_burst=2.0,
            host_rate=20.0,
            host_burst=4.0,
            max_connections_per_host=2,
        )
        decision = decide_auto_rate_policy(HubExposurePosture(token_configured=True), operator)
        assert decision.auto_enabled is False
        assert decision.filled == ()
        assert decision.limits == operator
        assert "already set by the operator" in decision.report_lines[0]

    def test_report_names_the_triggers_and_the_filled_limits(self) -> None:
        decision = decide_auto_rate_policy(
            HubExposurePosture(off_loopback_bind=True, token_configured=True), DISABLED
        )
        joined = "\n".join(decision.report_lines)
        assert "off-loopback bind" in joined
        assert "connect token configured" in joined
        assert "auto-filled bounded limits" in joined

    def test_partial_operator_config_fills_only_the_gaps(self) -> None:
        operator = RateLimits(host_rate=250.0, host_burst=50.0)
        decision = decide_auto_rate_policy(HubExposurePosture(multi_seat=True), operator)
        # host preserved, agent + connection cap filled.
        assert decision.limits.host_rate == 250.0
        assert decision.limits.host_burst == 50.0
        assert decision.limits.agent_rate == SECURE_AGENT_RATE
        assert decision.limits.max_connections_per_host == SECURE_MAX_CONNECTIONS_PER_HOST
        assert decision.filled == ("per-agent rate", "per-host connection cap")


class TestReplayCapacityEnvelope:
    def test_five_secure_principals_can_fill_the_default_live_window(self) -> None:
        decision = decide_auto_rate_policy(HubExposurePosture(multi_seat=True), DISABLED)
        limits = decision.limits
        auth = HubAuthConfig()

        def accepted_frame_envelope(principals: int) -> int:
            per_agent = principals * (
                limits.agent_burst + limits.agent_rate * auth.per_message_auth_window_seconds
            )
            per_host = limits.host_burst + limits.host_rate * auth.per_message_auth_window_seconds
            return int(min(per_agent, per_host))

        assert accepted_frame_envelope(4) == 4080
        assert accepted_frame_envelope(4) < auth.per_message_auth_replay_capacity
        assert accepted_frame_envelope(5) == 5100
        assert accepted_frame_envelope(5) > auth.per_message_auth_replay_capacity
        assert limits.max_connections_per_host >= 5
        assert (
            auth.per_message_auth_replay_capacity - limits.host_burst
        ) / limits.host_rate == pytest.approx(7.992)


class TestFrozenContracts:
    def test_posture_is_frozen(self) -> None:
        with pytest.raises((AttributeError, TypeError)):
            HubExposurePosture().off_loopback_bind = True  # type: ignore[misc]

    def test_decision_is_frozen(self) -> None:
        decision = decide_auto_rate_policy(HubExposurePosture(), DISABLED)
        with pytest.raises((AttributeError, TypeError)):
            decision.auto_enabled = True  # type: ignore[misc]
