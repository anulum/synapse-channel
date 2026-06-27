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

- A test suite across the `tests/` tree exercises every package module. The
  current test-function, module, and surface counts are in the README's capability
  inventory, which CI keeps in sync with the source tree.
- Coverage of the `synapse_channel` package is **100%** — no lines or branches
  missed, with branch coverage on. The gate fails below 95%; in practice it is held
  at 100%.
- Tests are deterministic: time is injected, transports are faked, and no test
  reaches the network.

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
| MCP surface audit | `tools/audit_mcp_surface.py --check` | documented tools/resources and adapter boundaries match registration |
| Release claim hygiene | `tools/check_release_claim_hygiene.py --check` | changelog/release prose has no agent-authorship, quality-label, or conformance overclaims |
| Commercial claim hygiene | `tools/check_commercial_claim_hygiene.py --check` | commercial docs preserve the AGPL/commercial boundary and no feature-split claims |
| Types | `mypy` (strict) | no errors |
| Tests + coverage | `pytest --cov` | pass, ≥ 95% (held at 100%) |
| Licensing | `reuse lint` | REUSE 3.x compliant |
| Spelling | `typos` | clean |
| Security lint | `bandit` | clean |
| Code scanning | CodeQL (CI) | no new alerts |

## Coverage policy

The coverage gate is configured in `pyproject.toml` (`[tool.coverage]`,
`fail_under = 95`). Branch coverage is on. The only excluded lines are the
standard non-executable guards (`if __name__ == "__main__":`, `if TYPE_CHECKING:`,
`pragma: no cover`); no functional code is excluded.

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
| `relay_token_benchmark` | Byte/token cost of the lite relay encoding vs the raw wire form | Lite is 1662 of 2826 bytes (59%) and 568 of 919 tokens (62%) over a 12-message trace |
| `routing_benchmark` | Task-class distribution and tiered-dispatch verification | 15 prompts → 4 rule / 4 slm / 7 heavy; dispatch verified |

Byte counts and routing decisions are exact and reproducible. Token counts use a
real tokeniser (`tiktoken`) when installed, with a labelled fallback. Per-tier
model latency is out of the offline scope — it needs a live model server — and is
documented as such rather than fabricated.

Run them with:

```bash
make bench
```
