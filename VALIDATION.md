<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — validation
-->

# Validation

This document records how SYNAPSE CHANNEL is tested and the gates a change must
clear. The figures below are reproducible from the repository.

## Test suite

- The `tests/` tree contains unit and real runtime profiles. Test-function,
  module and surface counts in the README inventory describe the source tree;
  they do not prove that every module, platform or deployment was exercised.
- CI enforces an aggregate line-and-branch coverage floor of **98%**. The
  Codecov report for `main` measured **98.85%** on 2026-07-18; its badge rounds
  that value to 99%, so the badge is not evidence that coverage exceeds 99%.
- Tests are designed to be deterministic: time and remote transports are injected
  where practical, while end-to-end cases use throwaway localhost services and
  external hardware or network paths remain explicitly gated.

Run them with:

```bash
make test          # the gate
make cov           # with a per-line missing-coverage report
```

## CI gates

Every change must clear these gates (run locally with `make preflight`):

| Gate | Tool | Enforcement |
| --- | --- | --- |
| Lint | `ruff check` (E, F, I, B, UP, D) | no warnings |
| Formatting | `ruff format --check` | no diff |
| MCP surface audit | `tools/audit_mcp_surface.py --check` | registered tools/resources and required documentation text match; host interoperability is separate |
| Release claim hygiene | `tools/check_release_claim_hygiene.py --check` | configured prohibited prose patterns are absent; runtime claim qualification remains separate |
| Commercial claim hygiene | `tools/check_commercial_claim_hygiene.py --check` | required licence-boundary and prohibited feature-split phrases are checked; this is not legal validation |
| Types | `mypy` (strict) | no errors |
| Tests + coverage | `pytest --cov` | pass, ≥ 98% |
| Licensing | `reuse lint` | REUSE 3.x compliant |
| Spelling | `typos` | clean |
| Security lint | `bandit` | clean |
| Code scanning | CodeQL (eligible GitHub runs) | analysis and finding upload; actual alert disposition is checked separately |

## Coverage policy

The coverage gate is configured in `pyproject.toml` (`[tool.coverage]`,
`fail_under = 98`). Branch coverage is on. Its configured exclusion patterns
cover `pragma: no cover`, the `__main__` guard, `TYPE_CHECKING`,
`raise NotImplementedError` and `@overload`/`@typing.overload`. A pragma or
matching pattern does not prove that code is non-executable. The coverage
exclusion ledger and its checks account for these exclusions separately;
aggregate coverage does not qualify unexecuted hardware, host or vendor paths.

A2A bridge modules also have a focused module-specific ratchet for the local
hardening lane. After running the focused A2A tests under coverage, generate a
JSON report with `coverage json --fail-under=0` and run:

```bash
.venv/bin/python tools/check_a2a_module_coverage.py .coverage-a2a.json
```

The ratchet requires 100% line and branch coverage for `a2a_server.py`,
`cli_a2a.py`, `a2a_events.py`, and `a2a_store.py`.

The A2A state-file durability matrix in `docs/cli.md` is covered by focused
store and lifecycle tests. Run the matrix checks directly with:

```bash
.venv/bin/python -m pytest tests/test_a2a_store.py tests/test_a2a_store_persistence.py tests/test_a2a_server_lifecycle.py::test_state_file_recovery_fails_stale_working_tasks -q
```

The bounded local A2A soak checks are deterministic functional tests, not
benchmarks. They cover real localhost HTTP churn, persistence churn, injected
webhook delivery failures, and subscriber fanout:

```bash
.venv/bin/python -m pytest tests/test_a2a_load_soak.py -q
```

The MCP adapter documentation is checked against the registered FastMCP surface
and required adapter/auth/dependency boundary text:

```bash
.venv/bin/python tools/audit_mcp_surface.py --check
```

The changelog and release-note prose are checked for public claim hygiene:

```bash
.venv/bin/python tools/check_release_claim_hygiene.py --check
```

The commercial documentation is checked for the AGPL/commercial boundary and for
claims that imply paid code paths absent from the public package:

```bash
.venv/bin/python tools/check_commercial_claim_hygiene.py --check
```

## Benchmarks

Benchmarks are runnable, committed scripts under `benchmarks/`, with their
results checked in under `benchmarks/results/`. No number in the documentation is
estimated by hand.

| Benchmark | What it measures | Result on the committed fixture |
| --- | --- | --- |
| `relay_token_benchmark` | Byte/token cost of the full-field lite relay encoding vs the raw wire form | Lite is 2498 of 2826 bytes (88%) and 807 of 919 `cl100k_base` tokens (88%) over `benchmarks/traces/sample_session.json`, a 12-message trace |
| `routing_benchmark` | Task-class distribution and tiered-dispatch verification | 15 prompts → 4 rule / 4 slm / 7 heavy; dispatch verified |

Byte counts and routing decisions are exact and reproducible. Token counts use a
real tokeniser (`tiktoken`) when installed, with a labelled fallback. Per-tier
model latency is out of the offline scope — it needs a live model server — and is
documented as such rather than fabricated.

The relay result is bound to `benchmarks/results/relay_token_benchmark.json`.
Full-envelope fidelity is checked at millisecond timestamp precision. The older
59% byte/62% token figures discarded auxiliary fields and are superseded; these
fixture numbers are not provider-context savings or a terminal-capacity test.

Run them with:

```bash
make bench
```

## Durable hub inbox recovery

`tests/test_hub_inbox.py` exercises admitted WebSocket reads, recipient
filtering, private-channel exclusion, mailbox ACLs, long gaps exceeding
one scan page and malformed queries. `tests/test_hub_inbox_clients.py`
exercises CLI cursors and the MCP bridge connection;
`tests/test_hub_inbox_network.py` probes untrusted replies and handshake refusals.
`tests/test_hub_inbox_cursor.py` checks real filesystem isolation,
owner-only persistence and refusals preserving prior state. The JavaScript
real-hub journey sends the same additive query. These checks establish
transport and reader consumption, not model processing or recovery beyond
retained journal history.
