<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Participant provider routing

`route_candidates(task, candidates)` evaluates every `ModelCandidate` and returns a
`RoutingDecision` with a choice and a rejection code for each excluded candidate.
`select_provider` retains its former choice-or-`None` interface. `orchestrate_session`
uses the decision path and records every decision in `OrchestrationTranscript.route_decisions`,
including an unroutable final round. The router does not query a vendor, validate a billing
account, or reserve spend. The operator supplies observations and their provenance.

## Declare evidence

`price_kind=PriceKind.FREE` is an explicit assertion that use of this candidate is free.
`price=ModelPrice(input_per_1k=..., output_per_1k=...)` is a priced quote, inferred as
`PriceKind.PRICED` for older callers. With neither, cost is **unknown** and
`RoutingChoice.estimated_cost` is `None`. A quoted zero remains a priced quote, not a
free assertion. Set `price_currency`, `price_revision`, `price_source`,
`price_observed_at`, and `price_valid_until` where known. Expiry is exclusive:
`now >= valid_until` means stale. A quote with no expiry is treated as current until the
operator refreshes it; time metadata alone cannot establish vendor truth.

`rate_limit_utilisation` is a fraction in `[0, 1]`; `None` means unknown. Set
`quota_source`, `quota_observed_at`, and `quota_valid_until` to make its origin and
freshness visible. A value of `1` is exhausted. Suspended or expired accounts cannot be
selected. Invalid numbers, timestamps, or contradictory free/price declarations are
rejected. Candidate `tags` and `data_classes` must satisfy the task even for a local
model; the default data class is only `public`.

## Choose uncertainty policy

```python
from synapse_channel.participants import (
    ModelCandidate,
    PriceKind,
    RoutingPolicy,
    TaskProfile,
    route_candidates,
)

task = TaskProfile(
    data_classification="private",
    estimated_input_tokens=2000,
    estimated_output_tokens=500,
    max_estimated_cost=0.50,
    currency="USD",
    policy=RoutingPolicy(
        unknown_price="refuse",
        stale_price="refuse",
        unknown_quota="refuse",
        stale_quota="refuse",
    ),
)
decision = route_candidates(task, candidates)
```

The four policy fields accept `"allow"` or `"refuse"`. Their default is `"allow"`
for compatibility with unbounded advisory routing, while unknown and stale evidence
ranks behind current evidence. For a task with `max_estimated_cost`, the router always
requires a current comparable quote or an explicit current free assertion, regardless
of those policy defaults. A currency mismatch, cost above the ceiling, or unverified
cost excludes the candidate. The ceiling applies to this one estimated turn; it is
neither a billing limit nor a reservation. Later entitlement and spend enforcement
must use a separate authoritative ledger.

`RouteRejection.code` reports a stable machine-readable reason such as
`unknown_price`, `stale_quota`, `cost_unverified`, `currency_mismatch`,
`account_suspended`, `data_policy`, or `missing_capability`. Inspect `decision.rejected`
before retrying or changing policy. No eligible provider yields `choice=None`; it
does not fall through to an unpriced remote provider.

## Migration

Older unpriced candidates remain eligible for **unbounded** routing under the
default advisory policy, but their cost is now `None` and they rank behind known
current quotes. To preserve an intentionally free local route, declare
`price_kind=PriceKind.FREE` and its allowed data classes explicitly. To require a
verified cost, set `max_estimated_cost` and populate current price and currency
evidence. Callers that need rejection diagnostics should migrate from
`select_provider` to `route_candidates` or read the orchestration transcript.

The [local account and quota ledger](entitlements.md) retains account, pool and
window evidence separately. This router does not consume that ledger or treat
its advisory forecast as a spending reservation. Cross-hub hard limits require
an authoritative Fleet allocation contract.

The [pi participant](pi.md) reports a model's final usage as observation only.
Selecting a pi model or enabling its claim guard does not turn those figures
into verified price evidence or a hard spend reservation.
