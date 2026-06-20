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

- **495 tests** across 23 test files under `tests/`, exercising every one of the
  21 package modules.
- Coverage of the `synapse_channel` package is **100%** (2108 statements, 598
  branches, 0 missed). The gate fails below 95%; in practice it is held at 100%.
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
