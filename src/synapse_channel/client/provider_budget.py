# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — explicit local estimated-spend control for paid model workers
"""Dated operator quotes and local spend reservation for paid provider calls.

This is an estimated local guard, not a provider billing limit. Provider-side
limits remain necessary for a hard financial cap.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ProviderQuote:
    """One dated per-million-token quote including billing dimensions."""

    provider: str
    model: str
    currency: str
    input_per_million: float
    output_per_million: float
    cache_read_per_million: float | None
    cache_write_per_million: float | None
    reasoning_per_million: float | None
    source_url: str
    source_date: str
    observed_at: float
    valid_until: float
    cache_write_5m_per_million: float | None = None
    cache_write_1h_per_million: float | None = None
    price_revision: str = ""
    region: str = ""
    tier: str = ""

    @classmethod
    def load(cls, path: str | Path) -> ProviderQuote:
        """Read a strict operator-owned JSON quote; missing dimensions stay unknown."""
        data: Any = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("provider quote must be a JSON object")
        quote = cls(**data)
        if not all(
            (
                quote.provider,
                quote.model,
                quote.currency,
                quote.source_url,
                quote.source_date,
                quote.price_revision,
            )
        ):
            raise ValueError("provider quote requires identity, currency and source")
        if quote.currency != "USD":
            raise ValueError("only USD quotes are currently comparable")
        source = urlsplit(quote.source_url)
        if source.scheme != "https" or not source.netloc:
            raise ValueError("provider quote requires an HTTPS source URL")
        try:
            source_day = date.fromisoformat(quote.source_date)
        except ValueError as exc:
            raise ValueError("provider quote requires an ISO source date") from exc
        if source_day > datetime.now(timezone.utc).date():
            raise ValueError("provider quote source date is in the future")
        values = (
            quote.input_per_million,
            quote.output_per_million,
            quote.cache_read_per_million,
            quote.cache_write_per_million,
            quote.cache_write_5m_per_million,
            quote.cache_write_1h_per_million,
            quote.reasoning_per_million,
        )
        if any(
            value is not None
            and (type(value) not in (int, float) or not math.isfinite(value) or value < 0)
            for value in values
        ):
            raise ValueError("provider quote contains invalid price")
        if quote.input_per_million == 0 and quote.output_per_million == 0:
            raise ValueError("paid provider quote cannot infer free use from zero prices")
        if type(quote.observed_at) not in (int, float) or type(quote.valid_until) not in (
            int,
            float,
        ):
            raise ValueError("provider quote timestamps must be numeric")
        if (
            not math.isfinite(quote.observed_at)
            or not math.isfinite(quote.valid_until)
            or quote.observed_at <= 0
            or quote.observed_at > time.time()
            or quote.valid_until <= quote.observed_at
            or quote.valid_until - quote.observed_at > 7 * 86400
        ):
            raise ValueError("provider quote has invalid validity interval")
        return quote

    def estimate(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate uncached input/output cost; cache/reasoning stay visible separately."""
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token forecasts must be nonnegative")
        return (
            input_tokens * self.input_per_million + output_tokens * self.output_per_million
        ) / 1_000_000


class EstimatedSpendGuard:
    """Reserve estimated request cost against one finite local worker budget."""

    def __init__(self, quote: ProviderQuote, *, budget_usd: float) -> None:
        if not math.isfinite(budget_usd) or budget_usd <= 0:
            raise ValueError("paid provider budget must be positive and finite")
        self.quote = quote
        self.budget_usd = budget_usd
        self.reserved_usd = 0.0
        self._lock = Lock()

    def reserve(
        self, *, provider: str, model: str, input_bytes: int, max_output_tokens: int
    ) -> float:
        """Reserve a conservative local estimate before sending a request.

        Input bytes plus 1024 protocol overhead form a conservative proxy for
        token count; this is not a guarantee of provider billing.
        """
        if provider != self.quote.provider or model != self.quote.model:
            raise ValueError("provider quote identity does not match the request")
        if not self.quote.observed_at <= time.time() < self.quote.valid_until:
            raise ValueError("provider quote is stale")
        if input_bytes < 0 or max_output_tokens < 1:
            raise ValueError("invalid request budget inputs")
        estimate = self.quote.estimate(input_bytes + 1024, max_output_tokens)
        with self._lock:
            if self.reserved_usd + estimate > self.budget_usd:
                raise ValueError("provider estimated budget exhausted")
            self.reserved_usd += estimate
        return estimate
