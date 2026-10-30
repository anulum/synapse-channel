<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Benchmarks

Runnable workloads and retained result artifacts for SYNAPSE CHANNEL. The
JSON files under `results/` record their named fixtures and available host/source
metadata. A result's figures qualify that recorded profile; the presence of a
script or JSON file does not establish a fresh run of a current build.

## `coordination_wire_benchmark.py`

Measures six fixed current JSON coordination frames, including claim, directed
chat, mailbox ACK and delivery receipt. It checks each production bounded-JSON
round trip and records default-wire versus compact bytes plus local median
encode/decode time. Run `PYTHONPATH=src python benchmarks/coordination_wire_benchmark.py`.
The committed result is `results/coordination_wire_benchmark.json`. It is a
synthetic codec baseline, not a network or provider benchmark.

## `relay_token_benchmark.py`

Measures how much the lite relay encoding shrinks a channel feed for a
token-budgeted observer. It replays a fixed trace of broadcast envelopes and
reports three serialisations of each message so the saving is decomposed, not
quoted as one inflated figure:

- **`raw_wire`** — the full envelope with default `json.dumps` spacing, exactly
  what the hub broadcasts on the socket.
- **`raw_compact`** — the same full envelope minified, isolating whitespace
  savings from the compact-key representation.
- **`lite`** — `encode_lite` output, minified, as written to the relay log.

Version 2 retains structured JSON payloads and every auxiliary field under a
compact extension mapping; the decoder continues to read version-1 rows.
Timestamps survive to the millisecond. `roundtrip_envelope_fidelity` compares
the reconstructed full envelope against an independent normalisation of the
input, so a dropped task, lease, presence, or grant field makes the benchmark
fail visibly.

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
| `raw_compact` | 2617 | — |
| `lite` | 2498 | 807 |

On this trace the lite log is **88%** of the raw wire bytes and **88%** of the
raw wire tokens. Against the same full envelope minified, it is **95%**. Full
envelope fidelity is exact at the documented millisecond timestamp precision.
The older 59% byte result counted discarded auxiliary fields as a saving and is
superseded. These figures are specific to this fixture — re-run against your own
trace for a representative number.

## `contract_conflict_benchmark.py`

Measures `synapse contracts check` against real merges of labelled scenarios
in disposable git repositories, including an interface change, a client and
a deployment on separate branches. Outcomes: caught 4, missed 3, false block 5,
clear 2, refused 1. Lost parallelism under a counterfactual "block on any
finding" policy is 5 of 7 mergeable scenarios. These are chosen cases, not a
rate. Details: [docs/benchmarks.md](../docs/benchmarks.md#contract-conflict-benchmark).

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
dispatch, credential-free delivery-evidence recording, and bounded subscriber
fanout. It is an in-process regression harness, not a third-party A2A
conformance or real webhook latency test.

### Run

```bash
python benchmarks/a2a_bridge_benchmark.py
```

### Committed result (250 tasks, 32 subscribers)

| Operation | Result |
| --- | ---: |
| Task creation | 4,187 tasks/s |
| Reply correlation + delivery evidence | 3,158 tasks/s |
| Task listing | 250 tasks |
| Push delivery callbacks | 250 deliveries |
| Push delivery evidence | 250 terminal successes |
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

## `native_message_record_benchmark.py`

Measures what one `native_message_record` costs. The parser is timed over a
256-byte and a 32,768-byte text, because the digest check grows with the text.
The durable round trip runs a real hub with an on-disk WAL journal on a loopback
port and one real WebSocket connection, and times each record from the frame to
the `native_message_recorded` reply; the journal is read back to confirm that
every record was stored. Each run stays inside the verb's own ingress quota (600
records and 8 MiB per minute and principal), which is the real ceiling of
sustained ingest and is reported with the result.

```bash
PYTHONPATH=src python benchmarks/native_message_record_benchmark.py
```

Output is written to `results/native_message_record_benchmark.json`. The
committed comparison records the host, system load, CPU affinity, governor,
source revision and isolation state.
The timed loop includes first sender registration, so changes to registration
require rerunning this benchmark even when record parsing is unchanged.

The current storage comparison executes the full default native workload
against `a1b6fe8d` and a frozen table-family candidate, using identical runtime
dependencies and quotas. The baseline and source-manifest/harness digests are
under `storage_comparison`. The sustained-write, scalability and coding-fleet
results were refreshed against the same variants. Runs are serial on shared
workstation CPU/storage, with no reserved cores or discarded warm-up. The
reported large-read/replay costs include additional ownership/dispatch
boundaries and the new partial MAC index; no performance improvement or
production claim is inferred from a single pair.

Previous frame-pipeline measurements remain under `historical_frame_pipeline`.
Its composition-root and library-probe measurements remain nested under
`historical_frame_pipeline.historical_composition_root`, with their original
source fingerprints. See [the comparison table](../docs/benchmarks.md#storage-transaction-and-replay-comparison).

## `sustained_write_benchmark.py`

Profiles the durable event store itself under sustained write load on a real on-disk
WAL database: write-latency distribution (mean / p50 / p95 / p99 / max) and throughput
for the default `synchronous=NORMAL` commit and the `durable=True` `synchronous=FULL`
fsync path; the `read_since(0)` replay cost as the retained log grows (an `O(events)`
scan); and how deleting the oldest half of the log (compaction) lowers read cost.

The recorded workload illustrates a *shape* — stable per-event write latency, a much
higher fsync-bound durable latency, a linear read scan, and a read cost that falls with
compaction — not the host-specific absolute times, which are recorded with the host CPU
and Python version. Other storage, load and count profiles require their own
measurements before relying on that shape.

### Run

```bash
python benchmarks/sustained_write_benchmark.py
# tune the run: --sustained-count --durable-count --read-counts --compaction-count
```

Output is written to `results/sustained_write_benchmark.json` with a one-line summary.

## `authenticated_write_benchmark.py`

Measures NORMAL and FULL appends on a fresh row-MAC-enabled WAL store with a
public fixed fixture key, then authenticates every row. Run
`PYTHONPATH=src python benchmarks/authenticated_write_benchmark.py`; use
`--normal-count` and `--durable-count` for smaller workloads. The three-pair
before/after record is under
`results/sustained_write_benchmark.json` →
`storage_comparison.authenticated_write_probe`. It measures partial MAC-index
maintenance on shared CPU/storage, without reading any enrolled operator key.

## `verified_completion_benchmark.py`

Runs real `worker-session` tasks and reads `verify-result file`. The worker
performs the fault. `confirmed_done` and `unknown` come from the outcome file.
The baseline matches. Wrong content and a missing file are reported done and
are not verified. A crash after the write leaves the outcome unknown while the
file still verifies. `--no-arm` keeps the hub sidecar out of the run. This does
not measure network faults, restart, duplicate delivery, or withdrawn
authority, and it is not GPT6-C9. Python only. Run
`PYTHONPATH=src python benchmarks/verified_completion_benchmark.py`. The record
is `results/verified_completion_benchmark.json`. Host timings live only in that
file.
