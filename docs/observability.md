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
   via `rule_files:`. Six rules ship: hub down, dead letters growing,
   claims denied faster than granted, auth failures, takeover quarantine,
   federation denials. The thresholds are starting points sized for a
   single-workstation fleet — tune them to your chatter before paging
   anyone at 03:00.

## Reading the decision counters

The counters count **decisions, not intents**: a denied claim the client
retries and wins counts once as denied and once as granted, because both
happened. The signals that matter operationally:

- `synapse_claims_denied_total` outpacing `synapse_claims_granted_total`
  is contention — `synapse causality contention` names the fight.
- `synapse_dead_letters` climbing means someone writes to a name nobody
  holds — `synapse doctor` names the unread addressee, `syn inbox --as`
  drains it, and the gauge falls when the addressee connects.
- Any movement on `synapse_auth_failures_total` or
  `synapse_federation_denied_total` on a locked-down hub deserves a look
  at `synapse event-query --kind error` — expected during key rotation,
  suspicious otherwise.
- `synapse_takeover_quarantines_total` moving means two waiters fought
  over one identity until the hub pinned it — `syn reap --stale` cleans
  the corpse that caused it.

## Traces

Coordination causality exports to OpenTelemetry independently of this
page: `synapse causality otel --out spans.json` writes span records with
deterministic ids, `--endpoint` pushes OTLP/HTTP to a collector, and
`--watch` keeps the projection live. See the CLI reference — that surface
reads the durable log, so it belongs to the analytics plane and works
with the hub down.
