# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for proportionate shared-secret connect authentication

from __future__ import annotations

from synapse_channel.core.auth import TokenAuthenticator


def test_iterable_tokens_allow_any_agent() -> None:
    auth = TokenAuthenticator(["s3cret"])
    assert auth.is_empty is False
    ok, message = auth.authenticate("s3cret", "FAST")
    assert ok and message == "Authenticated."


def test_missing_token_is_refused() -> None:
    auth = TokenAuthenticator(["s3cret"])
    ok, reason = auth.authenticate("", "FAST")
    assert not ok and "required" in reason


def test_invalid_token_is_refused() -> None:
    auth = TokenAuthenticator(["s3cret"])
    ok, reason = auth.authenticate("wrong", "FAST")
    assert not ok and "Invalid" in reason


def test_per_agent_binding_admits_named_and_refuses_others() -> None:
    auth = TokenAuthenticator({"tok": ["FAST", "REASON"]})
    assert auth.authenticate("tok", "FAST")[0] is True
    ok, reason = auth.authenticate("tok", "INTRUDER")
    assert not ok and "not authorised for agent 'INTRUDER'" in reason


def test_empty_agent_set_permits_any_agent() -> None:
    auth = TokenAuthenticator({"tok": ()})
    assert auth.authenticate("tok", "ANYONE")[0] is True


def test_blank_token_is_dropped_leaving_authenticator_empty() -> None:
    auth = TokenAuthenticator([""])
    assert auth.is_empty is True
    ok, reason = auth.authenticate("anything", "A")  # no token to match
    assert not ok and "Invalid" in reason


def test_non_ascii_token_matches_and_mismatches() -> None:
    auth = TokenAuthenticator(["pärli"])
    assert auth.authenticate("pärli", "A")[0] is True
    assert auth.authenticate("parli", "A")[0] is False


def test_quota_principal_is_stable_across_names_but_distinct_per_token() -> None:
    auth = TokenAuthenticator(["alpha-secret", "beta-secret"])

    alpha_a = auth.authenticate_with_principal("alpha-secret", "A")
    alpha_alias = auth.authenticate_with_principal("alpha-secret", "A-ROTATED")
    beta = auth.authenticate_with_principal("beta-secret", "B")

    assert alpha_a[:2] == (True, "Authenticated.")
    assert alpha_a[2] is not None
    assert alpha_a[2] == alpha_alias[2]
    assert alpha_a[2] != beta[2]
    assert "alpha-secret" not in alpha_a[2]


def test_refused_authentication_has_no_quota_principal() -> None:
    auth = TokenAuthenticator({"secret": ["A"]})
    assert auth.authenticate_with_principal("wrong", "A")[2] is None
    assert auth.authenticate_with_principal("secret", "B")[2] is None
