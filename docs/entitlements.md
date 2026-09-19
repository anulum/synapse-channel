<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Local account and quota ledger

`synapse entitlements` stores account and quota evidence in a separate owner-only
SQLite ledger at `$XDG_STATE_HOME/synapse-channel/entitlements/ledger.sqlite3`
(normally `~/.local/state/synapse-channel/entitlements/ledger.sqlite3`). It does not write account names or
balances to the shared hub event log. The `show` and `history` commands are
local owner operations. A registered MCP read tool exposes only the count of
accounts and pools; it withholds identities, labels, products, balances, sources
and credential references. Neither view grants spending or routing authority.

## Record evidence

Write one JSON event to an owner-only file (mode `0600` on POSIX), then run:

```sh
synapse entitlements record --file account.json
synapse entitlements show
synapse entitlements history
```

The input must contain `event_id`, `kind`, `recorded_at`, `source` and
`confidence`. `recorded_at` and all other times require an explicit UTC offset;
quota windows use half-open `[starts_at, ends_at)` boundaries. Confidence is
`official`, `operator` or `inferred`; it is provenance, not a guarantee that a
vendor's current balance is known. The JSON field set is strict. Every quantity
is a finite, non-negative **decimal string** in the pool's declared unit.
Currency, token, percentage and call units remain separate pools and are never
added into a combined balance. Product surfaces (Chat, coding CLI and API) are
separate unless an operator has evidence that they genuinely draw from the same
pool. Never place a password or token in an event. The optional `credential_ref`
is a reference to existing secret handling and must start with `ref:`.

An account event creates an opaque account id, a private label and status
(`active`, `suspended` or `expired`). A pool belongs to one account and declares
one unit. A surface belongs to the pool and account. A window declares one
grant, unit, price revision and bounded time interval. Usage and balance
observations refer to both a window id and the exact window event revision.
Their `source_event_id` must be unique within that pool window. The same
underlying transaction seen through two product surfaces is recorded **once**
at the pool window. Replaying the same event id with identical content does
nothing; reusing it for different content is refused.

For example, an operator can enter an account event:

```json
{
  "event_id": "account-entry-1",
  "kind": "account",
  "recorded_at": "2026-09-19T10:00:00+02:00",
  "source": "operator:billing-owner",
  "confidence": "operator",
  "account_id": "account-opaque-1",
  "label": "My private coding account",
  "status": "active"
}
```

Then create its pool, surfaces and window before adding observations:

| `kind` | Required fields beyond the common fields | Optional fields |
| --- | --- | --- |
| `account` | `account_id`, `label`, `status` | `credential_ref`, `expires_at` |
| `pool` | `pool_id`, `account_id`, `unit` | — |
| `surface` | `surface_id`, `account_id`, `pool_id`, `product`, `channel` | — |
| `window` | `window_id`, `pool_id`, `starts_at`, `ends_at`, `grant`, `unit`, `price_revision` | `renewal_at` |
| `usage` | `window_id`, `window_event_id`, `source_event_id`, `amount`, `observed_at` | — |
| `balance` | `window_id`, `window_event_id`, `source_event_id`, `remaining`, `observed_at` | — |

`balance.remaining` may be `null` to express a missing provider balance. The
common optional field is `supersedes` for an explicit correction. Invalid
references, overlapping windows and mismatched units are refused. CLI
`--store PATH` can select another private ledger, whose parent directory must
be owner-only.

## Import local Ollama token telemetry

Ollama's [generate](https://docs.ollama.com/api/generate) and
[chat](https://docs.ollama.com/api/chat) APIs expose `prompt_eval_count` and
`eval_count` in the final response. Capture one response with `stream: false`
in an owner-only JSON file, after creating a `tokens` pool and a window that
covers its `created_at` time. Then run:

```sh
synapse entitlements observe-ollama --file ollama-response.json \
  --window-id my-window --window-event-id my-window-revision
```

The command imports the sum of input and output token counts as one usage
observation. It stores a hash of the exact response as its source identity, so
reimporting the file is idempotent. It does **not** store the response text,
prompt, messages, reasoning or tool calls. Keep the captured input file private
and remove it under your own retention policy. A partial stream, missing count,
negative count, invalid timestamp or wrong window is refused. These are local
model usage counts, not a vendor quota or price; the operator must supply any
grant and must verify that the source belongs to the chosen pool.

## Correct facts without erasing history

To correct a fact, submit another event with a new `event_id`, later
`recorded_at`, the same logical target and `supersedes` set to the prior event
id. `history` retains both records and their distinct sources. A corrected
window carries a new event revision; old balance samples remain in history but
do not contribute to a forecast for the new price or reset revision. New quota
periods use a new, non-overlapping window id.

## Interpret the report

`show` reports per-pool windows and surfaces. A `remaining` value derived only
from recorded usage is labelled `incomplete_usage_estimate`; it is not a vendor
balance. A missing latest balance is `unknown`. Suspended or expired accounts
have `account_usable=false`. Forecasts require at least three current-revision
balance observations covering an hour. The report includes sample count,
observation age, confidence and a reason when data are insufficient, stale,
quiet, replenished or changed. A burst can lower confidence. The forecast is
advisory and never acts as a reservation or hard spend limit.

The ledger is local to one owner. Cross-hub reservations, authoritative
distributed limits and federated projections require Fleet's separate
admission and review gates. Operator-entered vendor figures must be refreshed
from current official evidence; Synapse does not infer plan allowances or
prices from product names.
