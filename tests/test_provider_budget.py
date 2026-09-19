# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — local paid-provider quote and reservation contracts
"""Verify stale, mismatched and exhausted quotes fail before network egress."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from http_server_helpers import LocalHttpResponder
from synapse_channel.client.provider_budget import EstimatedSpendGuard, ProviderQuote
from synapse_channel.client.provider_http import ProviderHTTPClient, ProviderWorkerBackend
from synapse_channel.client.provider_profiles import PROFILES


def _quote(path: Path, *, valid_until: float | None = None) -> ProviderQuote:
    now = time.time()
    path.write_text(
        json.dumps(
            {
                "provider": "openai",
                "model": "model-one",
                "currency": "USD",
                "input_per_million": 1.0,
                "output_per_million": 2.0,
                "cache_read_per_million": None,
                "cache_write_per_million": None,
                "reasoning_per_million": None,
                "source_url": "https://openai.com/api/pricing/",
                "source_date": "2026-09-19",
                "price_revision": "2026-09-19-manual",
                "observed_at": now - 60,
                "valid_until": valid_until or now + 3600,
            }
        ),
        encoding="utf-8",
    )
    return ProviderQuote.load(path)


def test_paid_worker_reserves_before_local_http(tmp_path: Path) -> None:
    """A paid profile reaches localhost only with a matching dated quote."""
    quote = _quote(tmp_path / "quote.json")
    budget = EstimatedSpendGuard(quote, budget_usd=0.01)
    body = b'{"choices":[{"message":{"content":"hello"}}]}'
    with LocalHttpResponder(body=body) as server:
        client = ProviderHTTPClient(PROFILES["openai"], api_key="k", base_url=server.url)
        worker = ProviderWorkerBackend(client, model="model-one", budget=budget)
        assert worker.generate(system_prompt="s", user_prompt="u") == "hello"
    assert budget.reserved_usd > 0
    assert len(server.requests) == 1


def test_paid_worker_exhaustion_sends_no_second_request(tmp_path: Path) -> None:
    """The local finite estimate budget gates subsequent requests."""
    quote = _quote(tmp_path / "quote.json")
    estimate = quote.estimate(1026, 1024)
    budget = EstimatedSpendGuard(quote, budget_usd=estimate * 1.5)
    body = b'{"choices":[{"message":{"content":"hello"}}]}'
    with LocalHttpResponder(body=body) as server:
        client = ProviderHTTPClient(PROFILES["openai"], api_key="k", base_url=server.url)
        worker = ProviderWorkerBackend(client, model="model-one", budget=budget)
        worker.generate(system_prompt="s", user_prompt="u")
        with pytest.raises(ValueError, match="exhausted"):
            worker.generate(system_prompt="s", user_prompt="u")
    assert len(server.requests) == 1


def test_quote_identity_and_staleness_fail_closed(tmp_path: Path) -> None:
    """Unknown model prices and expired evidence cannot be spent."""
    quote = _quote(tmp_path / "quote.json")
    guard = EstimatedSpendGuard(quote, budget_usd=1.0)
    with pytest.raises(ValueError, match="identity"):
        guard.reserve(provider="openai", model="other", input_bytes=1, max_output_tokens=1)
    stale = ProviderQuote(**{**quote.__dict__, "valid_until": time.time() - 1})
    with pytest.raises(ValueError, match="stale"):
        EstimatedSpendGuard(stale, budget_usd=1.0).reserve(
            provider="openai", model="model-one", input_bytes=1, max_output_tokens=1
        )


def test_zero_price_cannot_implicitly_authorize_paid_calls(tmp_path: Path) -> None:
    """A zero-filled quote is not proof that a paid provider is free."""
    path = tmp_path / "quote.json"
    _quote(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["input_per_million"] = 0
    data["output_per_million"] = 0
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="infer free"):
        ProviderQuote.load(path)


def test_paid_remote_endpoint_requires_https() -> None:
    """A customer key must not be sent over remote cleartext HTTP."""
    with pytest.raises(ValueError, match="HTTPS"):
        ProviderHTTPClient(PROFILES["openai"], api_key="k", base_url="http://example.test/v1")
