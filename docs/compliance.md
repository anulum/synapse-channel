<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — retained-log record observations
-->

# Retained-log record observations

`synapse compliance` reads an existing local event journal and emits a record
bundle, a calendar retention observation, or both. It contacts no hub and makes
no coordination decision. It does not classify a system or certify compliance.

```bash
synapse compliance export ./hub.db --json
synapse compliance retention ./hub.db --as-of 1791961200 --json
synapse compliance report ./hub.db --framework eu-ai-act --json
```

## Snapshot and authentication

Each result reads one SQLite transaction in ascending sequence order. Concurrent
appends after that snapshot do not enter its commitment. The reader opens the
existing journal in read-only mode: it does not create a missing database,
migrate tables, backfill aggregates, quarantine rows or alter file modes.
SQLite may create WAL coordination sidecars while an active journal is read.

The bundle commits full selected events with the existing RFC 6962 Merkle
primitive, while printing only sequence, timestamp, kind and indicative
category metadata. Payloads and key material are omitted. Metadata can still be
sensitive; choose storage and sharing permissions appropriate to the deployment.
Keep the original selected journal for recomputation: metadata alone cannot
reconstruct the root. A root is neither a signed witness nor evidence that
every historical event was recorded or retained.

Authenticated journals require the trusted enrolled key explicitly:

```bash
synapse compliance export ./hub.db \
  --row-mac-key-file ./hub.db.rowmac.key --json
```

Every selected MAC-bearing row is checked. A missing MAC after the key's enrolled
sequence or an invalid MAC refuses the complete result. Older rows without a MAC
remain explicitly unverified. Authentication counts appear in JSON and human
summaries; row authentication and a Merkle commitment are separate observations.
Existing owner-only key rules apply. The reader never generates a replacement
key or automatically treats an enrolled journal as unauthenticated.

Use `--db-key-file` with an existing owner-only key for SQLCipher journals. The
optional native SQLCipher backend must be installed; unavailable backends,
wrong keys and unreadable databases refuse the command. No plaintext fallback
is attempted for a keyed read.

## Complete selection and budgets

All three verbs accept `--through-seq N`, `--max-events N` and
`--max-payload-bytes N`. A ceiling is inclusive; zero selects an empty prefix.
The default is the whole snapshot, bounded by 100,000 events and 67,108,864 bytes
of encoded payload. Budgets must be positive and the ceiling nonnegative,
within SQLite's signed 64-bit sequence range.

Budget exhaustion, malformed payload JSON, duplicate object fields, non-finite
numbers, invalid row metadata or failed authentication refuse the entire
result. Rows are never silently skipped and a partial bundle is never printed.
Raise budgets explicitly for a larger complete export, using deployment resource
limits appropriate to that size. The Merkle fold holds logarithmic state;
collected metadata and JSON output require linear memory. These limits bound
event count and encoded payload, not arbitrary operating-system memory usage.

Success exits zero. Observation refusal exits two with a fixed diagnostic that
does not expose caught exception text, private paths or secrets. Argument syntax
errors also exit two. Human output summarizes the selected bundle; `--json`
contains every selected entry's metadata.

## Calendar observation

Retention and report accept `--as-of UNIX_SECONDS`; the default uses current
wall time. The earliest and latest timestamps are chronological extrema,
independent of sequence order. Reference time and event times must be finite and
representable in the UTC calendar.

The observation computes the oldest event's six-calendar-month anniversary,
preserving its UTC time of day and clamping an absent day to month end. For
example, 31 August 2025 becomes 28 February 2026; 30 August 2023 becomes
29 February 2024. This is an explicit UTC comparator, not a legal-deadline
calculation. It does not substitute 183 days for six months.

An empty snapshot has no measured window. Future event timestamps relative to
the chosen clock make the reach unconfirmed, even when older events exist.
A short window cannot distinguish a new deployment from past pruning. An old
retained event does not prove continuous logging or that every record survived.
The command performs no deletion, scheduling or retention-policy configuration.

## Framework scope and primary sources

The sole supported report label is `eu-ai-act`. Indicative risk-related and
operation categories link coordination records to Article 12(2)(a) and (c).
Other kinds remain general records. The categories do not establish regulatory
risk, a substantial modification, or complete post-market monitoring. This
surface does not implement the biometric requirements of Article 12(3).

Article 12 concerns automatic logging for applicable high-risk systems.
Articles 19(1) and 26(6) concern retained logs under the provider's or deployer's
control, with an appropriate period of at least six months unless other
applicable law, including data protection, provides otherwise. These conditions
require an operator's deployment-specific assessment.

The primary texts are the Commission's consolidated AI Act Service Desk pages:
[Article 12](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-12),
[Article 19](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-19) and
[Article 26](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-26).
The framework label supplies evidence interpretation aids and no legal verdict.

## Python API and compatibility

```python
from synapse_channel.core.compliance import build_report, report_to_json

report = build_report("hub.db", framework="eu-ai-act", now=1791961200)
data = report_to_json(report)
```

`core.record_keeping_source.iter_record_source` exposes the validated snapshot
iterator; close it explicitly when stopping early. `core.record_keeping` owns
bundle construction, JSON and human rendering. `core.record_retention` owns the
calendar observation. `core.compliance` composes reports and re-exports those
public bundle and retention functions.

The bundle schema is `eu-ai-act-record-keeping.v1`. It includes row authentication
provenance and chronological extrema. Retention fields expose `minimum_months`,
`as_of`, `anniversary`, `future_events` and `reaches_floor`; no day-based legal
floor is claimed. This is a Python local-journal reader, without a new wire
operation or a language-SDK counterpart. The dashboard has no controls for this
feature; use the documented CLI or API to obtain the observations.
