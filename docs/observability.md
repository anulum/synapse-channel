# Observability: Prometheus, Grafana, and the honest boundary

SYNAPSE does not ship its own Grafana — it makes the hub a first-class
citizen of the observability stack you already run. One flag exposes the
metrics, one scrape job collects them, one committed dashboard renders
them, and one rules file pages you when the fleet misbehaves.

## What lives where

Two planes, deliberately separate:

- **`/metrics` on the hub** (this page) — the **live process deciding**:
  connection gauges and monotonic decision counters (claims granted and
  denied, releases, directed and broadcast chat, auth failures, rate-limit
  rejections, federation denials, waiter takeovers and quarantines, the
  dead-letter ledger). Scrape-safe: reading it does no I/O. Counters reset
  with the process — Prometheus `rate()`/`increase()` expect exactly that.
  The `synapse_journal_corrupt_rows` gauge is the exception that describes
  startup recovery state: non-zero makes `/health` report `degraded` and the hub
  refuses mutations until an explicit archived offline repair.
- **The dashboard's store feeds and the cockpit** — **log analytics**:
  `/reliability.json`, `/events.json`, `/causality.json`, `/metrics.json`
  are derived from the durable event store, deterministic over a given
  log, and available with the hub down. They answer *what happened*;
  `/metrics` answers *what the process is doing right now*. Neither
  duplicates the other, and each document says which one it is.

## Five minutes to a working dashboard

1. **Expose the metrics.** Start the hub with `--metrics` (and, off
   loopback, `--metrics-token`):

   ```bash
   synapse hub --metrics --metrics-token "$(cat ~/.synapse-metrics-token)"
   curl -H "Authorization: Bearer $(cat ~/.synapse-metrics-token)" \
     http://localhost:8876/metrics | head
   ```

2. **Scrape.** Merge
   [`integrations/observability/prometheus-scrape.yml`](https://github.com/anulum/synapse-channel/blob/main/integrations/observability/prometheus-scrape.yml)
   into your `prometheus.yml` `scrape_configs:`. With a metrics token, use
   `credentials_file` — never inline the secret in the config.

3. **Import the dashboard.** In Grafana: Dashboards → Import → upload
   [`grafana-dashboard-synapse-hub.json`](https://github.com/anulum/synapse-channel/blob/main/integrations/observability/grafana-dashboard-synapse-hub.json)
   and pick your Prometheus datasource. You get liveness stats, message
   and claim-decision rates, refusal counts, the dead-letter ledger, and
   takeover/quarantine activity.

4. **Alert.** Load
   [`prometheus-alerts.yml`](https://github.com/anulum/synapse-channel/blob/main/integrations/observability/prometheus-alerts.yml)
   via `rule_files:`. Eight rules ship: hub down, dead letters growing,
   claims denied faster than granted, auth failures, takeover quarantine,
   federation denials, and — from the log-derived textfiles below —
   reliability findings and causal-health anomalies. The thresholds are
   starting points sized for a
   single-workstation fleet — tune them to your chatter before paging
   anyone at 03:00.

## Reading the decision counters

The counters count **decisions, not intents**: a denied claim the client
retries and wins counts once as denied and once as granted, because both
happened. The signals that matter operationally:

- `synapse_claims_denied_total` outpacing `synapse_claims_granted_total`
  is contention — `synapse causality contention` names the fight.
- `synapse_journal_corrupt_rows` must be zero on a writable hub. A positive
  value means replay quarantined malformed durable rows: queries remain live,
  mutations fail closed, and the operator must follow the archived
  `synapse compact --drop-corrupt` recovery runbook in the CLI reference.
- `synapse_dead_letters` climbing means someone writes to a name nobody
  holds — `synapse doctor` names the unread addressee, `syn inbox --as`
  drains it, and the gauge falls when the addressee connects. Its companion
  `synapse_dead_letter_targets` counts the distinct names currently
  blackholed; the ledger ages quiet names out, so a value that stays above
  zero (the `SynapsePersistentDeadLetters` alert watches it over an hour) is
  a persistent gap, not a passing miss. `synapse dead-letters` lists them.
  A hub given a `dead_letter_escalation_threshold` turns that passive gauge
  into an active signal: it broadcasts a notice and journals a
  `dead_letter_escalation` audit event each time a target's undelivered count
  crosses the threshold. When the blackholed target's namespace is owned by a
  peer hub — resolved through the same relay routes an operator relay uses —
  the escalation also forwards a pointer to that peer (the target and its
  count, never a message body) over the federation transport and records a
  `dead_letter_forwarding` audit event, so the hub that can actually reach the
  missing reader learns of the gap. The forward leaves a matching audit on both
  hubs, distinguished by a `direction` field — `out` on the origin that sent it,
  `in` on the owning hub that received it (naming the verified sending peer) —
  and the owning hub broadcasts the incoming pointer to its own operators. Query
  both with `synapse event-query --kind dead_letter_escalation` and
  `--kind dead_letter_forwarding`.
- **Mailbox pending is not the dead-letter ledger.** Dead letters record the
  send-time fact that no socket matched; the mailbox projection counts durable
  directed chats after an identity's receiver-acknowledged watermark, even across
  hub restart. `synapse who`, `synapse status`, and `synapse doctor` render
  `N undelivered messages pending for <identity>` from the additive WHO snapshot;
  to keep old test/diagnostic identities from flooding an operator terminal, a
  full `who` view sorts positive counts largest-first and shows the top 20 plus
  total identities/messages. `synapse who --all-mailbox-pending` (or `--all`)
  expands the complete retained projection; `--project` filters before the bound.
  A mailbox-enabled `synapse arm` replay advances the watermark, while
  `syn inbox --as <identity>` remains the human-readable body inspection path.
  A hub without a durable event store reports the projection unavailable instead
  of zero. The watermark proves only that a mailbox client accepted the
  frame—never that a model read or acted on it.
  The hub projection itself retains at most 512 recently touched identities.
  Eviction removes only a cached count, not journal evidence or message bodies;
  durable journal compaction remains an explicit operator retention decision.
- Any movement on `synapse_auth_failures_total` or
  `synapse_federation_denied_total` on a locked-down hub deserves a look
  at `synapse event-query --kind error` — expected during key rotation,
  suspicious otherwise.
- `synapse_takeover_quarantines_total` moving means two waiters fought
  over one identity until the hub pinned it — `syn reap --stale` cleans
  the corpse that caused it.

## Log-derived signals (the analytics plane, in Prometheus)

The store-derived analytics — reliability findings and causal-health
anomalies — reach the same Prometheus and alerting plane through the
`node_exporter` **textfile collector**, so they show up next to the live
counters without the hub needing to be up:

```bash
# a timer writes these into node_exporter's --collector.textfile.directory
synapse reliability ~/synapse/hub.db --textfile /var/lib/node_exporter/synapse_reliability.prom
synapse causality health ~/synapse/hub.db --textfile /var/lib/node_exporter/synapse_health.prom
```

The reliability file carries `synapse_reliability_findings{kind=...}` (all
four evidence kinds, zero included so an alert can fire the first time a
kind goes positive) and `synapse_reliability_owner_findings{owner,kind}`.
The health file carries `synapse_causal_health_anomalies{shape=...}`
(orphaned/dangling/stale), its total, and tasks scanned. Every value is
deterministic over a given log — the same log renders the same file — and
every file is valid exposition (the suite parses each back with the
Prometheus client parser, so node_exporter never silently rejects one).
These are **evidence gauges, not grades**: alert on a positive count, then
read the finding with `synapse reliability` or `synapse causality health`
to see what it is.

## Traces

Coordination causality exports to OpenTelemetry independently of this
page: `synapse causality otel --out spans.json` writes span records with
deterministic ids, `--endpoint` pushes OTLP/HTTP to a collector, and
`--watch` keeps the projection live. See the CLI reference — that surface
reads the durable log, so it belongs to the analytics plane and works
with the hub down.

## One fleet scorecard

`synapse fleet-scorecard ~/synapse/hub.db --out fleet-scorecard.json`
composes those durable analytics with opt-in accounting, advisory live-claim
contention, evidence-only reliability findings, and optional benchmark history
(`--trend bench-trend.db`). The portable JSON retains the complete source
reports and is written atomically with owner-only permissions.

With `pip install 'synapse-channel[otel]'`, point the same command at an
OTLP/HTTP collector base:

```bash
synapse fleet-scorecard ~/synapse/hub.db \
  --service-name workstation-hub \
  --endpoint http://127.0.0.1:4318
```

The command appends `/v1/traces` and `/v1/metrics`, sends the existing
deterministic task traces, and publishes scorecard gauges for opt-in calls,
tokens and cost, conflict pairs, reliability finding counts, trace volume, and
the latest comparable benchmark values. It does not collect usage itself, rank
agents, pre-empt claims, or backfill historic metric timestamps. The two OTLP
posts are fail-visible but not transactional: a failure exits nonzero even if
the collector accepted the other signal.
