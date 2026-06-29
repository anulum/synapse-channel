<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Benchmarks

Runnable, committed measurements for SYNAPSE CHANNEL. Every number quoted in the
docs comes from a script here run against a committed fixture, with the JSON
output checked in under `results/` — nothing is estimated by hand.

## `relay_token_benchmark.py`

Measures how much the lite relay encoding shrinks a channel feed for a
token-budgeted observer. It replays a fixed trace of broadcast envelopes and
reports three serialisations of each message so the saving is decomposed, not
quoted as one inflated figure:

- **`raw_wire`** — the full envelope with default `json.dumps` spacing, exactly
  what the hub broadcasts on the socket.
- **`raw_core_compact`** — only the seven core envelope fields the lite format
  keeps, minified. Isolates the short-key win from the field-dropping win.
- **`lite`** — `encode_lite` output, minified, as written to the relay log.

The lite format is intentionally lossy: it carries the seven core fields
(`sender`, `target`, `type`, `payload`, `timestamp`, `msg_id`, `hub_id`) and
drops auxiliary fields such as `task_id` or `paths`. So part of the reduction
comes from dropping fields and part from shorter keys plus minification — the two
raw baselines keep those effects separate. Timestamps survive only to the
millisecond; `roundtrip_core_fidelity` records whether the seven core fields
reconstruct exactly at that precision.

### Metrics

- **Bytes** are exact and dependency-free — the headline metric.
- **Tokens** use `tiktoken`'s `cl100k_base` when installed; without it the script
  falls back to a deterministic, clearly-labelled characters-per-token estimate
  and records which method produced the numbers in the `tokenizer` field. Install
  the real tokeniser with `pip install -e ".[benchmark]"`.

### Run

```bash
python benchmarks/relay_token_benchmark.py
# or against another trace / output path:
python benchmarks/relay_token_benchmark.py --trace path/to/trace.json --results path/to/out.json
```

Output is written to `results/relay_token_benchmark.json` and a short summary is
printed.

### Committed result (sample_session.json, 12 messages, cl100k_base)

| Serialisation | Bytes | Tokens |
| --- | --- | --- |
| `raw_wire` | 2826 | 919 |
| `raw_core_compact` | 1913 | — |
| `lite` | 1662 | 568 |

On this trace the lite log is **59%** of the raw wire bytes and **62%** of the
raw wire tokens. Holding the field set fixed, short keys plus minification alone
account for the `lite`-vs-`raw_core_compact` ratio (**87%**); the rest of the
reduction is the lite format dropping auxiliary fields an observer does not need.
Core-field roundtrip fidelity is exact. These figures are specific to this
fixture — re-run against your own trace for a representative number.

## `routing_benchmark.py`

Measures how the task-class router (`synapse_channel.routing.classify`) sorts a
fixed prompt set into `rule`, `slm`, and `heavy`, and verifies that a
`TieredChatClient` dispatches each prompt to the backend for its class. The
output is the class distribution, the per-prompt decision, and a
`routing_verified` flag — all exact and reproducible.

Backend *latency* is intentionally not measured here: the `slm` and `heavy`
tiers need a live model server, so timing them is not reproducible offline. The
committed numbers are the routing decisions only; benchmark real per-tier latency
against your own model server.

### Run

```bash
python benchmarks/routing_benchmark.py
```

### Committed result (routing_prompts.json, 15 prompts)

| Class | Prompts |
| --- | --- |
| `rule` | 4 |
| `slm` | 4 |
| `heavy` | 7 |

Tiered dispatch verified. The split is specific to this prompt set and the
default thresholds — tune `rule_max_chars`/`heavy_min_chars` for your workload.

## `a2a_bridge_benchmark.py`

Measures local Agent2Agent bridge costs without a network server: task creation,
SYNAPSE reply correlation, bridge-local task listing, push-delivery callback
dispatch, and bounded subscriber fanout. It is an in-process regression harness,
not a third-party A2A conformance or real webhook latency test.

### Run

```bash
python benchmarks/a2a_bridge_benchmark.py
```

### Committed result (250 tasks, 32 subscribers)

| Operation | Result |
| --- | ---: |
| Task creation | 7,763 tasks/s |
| Reply correlation | 19,771 tasks/s |
| Task listing | 250 tasks |
| Push delivery callbacks | 250 deliveries |
| Subscriber fanout | 32 terminal events |

## `coding_fleet_benchmark.py`

Measures a deterministic five-agent coding fleet over the real in-memory claim,
scope-conflict, release, and journal replay code. The scenario performs seven
claim attempts: five disjoint edits are granted and two deliberate overlapping
file-scope attempts are refused. The output records the conflict rate, claim
latency, release cleanup, replay recovery, attempt details, and the benchmark
limitations.

This is a local functional benchmark. It does not measure model latency, editor
integration latency, external services, or comparative performance against
remote coding-agent products.

### Run

```bash
python benchmarks/coding_fleet_benchmark.py
```

### Committed result (five agents, seven claim attempts)

| Metric | Result |
| --- | ---: |
| Granted claims | 5 |
| Refused overlaps | 2 |
| Conflict rate | 28.57% |
| Replay events | 10 |
| Post-release claims | 0 |

## `sustained_write_benchmark.py`

Profiles the durable event store itself under sustained write load on a real on-disk
WAL database: write-latency distribution (mean / p50 / p95 / p99 / max) and throughput
for the default `synchronous=NORMAL` commit and the `durable=True` `synchronous=FULL`
fsync path; the `read_since(0)` replay cost as the retained log grows (an `O(events)`
scan); and how deleting the oldest half of the log (compaction) lowers read cost.

The reproducible finding is the *shape* — a stable per-event write latency, a much
higher fsync-bound durable latency, a linear read scan, and a read cost that falls with
compaction — not the host-specific absolute times, which are recorded with the host CPU
and Python version.

### Run

```bash
python benchmarks/sustained_write_benchmark.py
# tune the run: --sustained-count --durable-count --read-counts --compaction-count
```

Output is written to `results/sustained_write_benchmark.json` with a one-line summary.
