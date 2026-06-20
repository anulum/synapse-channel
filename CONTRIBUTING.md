<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — contribution guide
-->

# Contributing to SYNAPSE CHANNEL

Thank you for considering a contribution. This guide covers how to set up a
development environment and the standards a change must meet to be merged.

## Getting started

1. Fork and clone the repository.
2. Create a virtual environment and install the package with its dev toolchain:

   ```bash
   python -m venv .venv
   . .venv/bin/activate
   pip install -e ".[dev,benchmark]"
   ```

3. Install the git hooks so style and hygiene checks run on every commit:

   ```bash
   make install-hooks
   ```

## Development workflow

The `Makefile` wraps the common tasks (`make help` lists them):

| Target | What it does |
| --- | --- |
| `make lint` | ruff lint + format check |
| `make fmt` | auto-fix lint and apply formatting |
| `make typecheck` | strict `mypy` |
| `make test` | the test suite with the coverage gate |
| `make cov` | tests with a per-line missing-coverage report |
| `make reuse` | SPDX/REUSE licensing compliance |
| `make preflight` | the full local gate before a commit |

## Standards a change must meet

- **Tests.** Every new module, function, and branch ships with tests. Coverage
  is held at 100% (the gate fails below 95%); a change must not lower it.
- **Types.** `mypy` runs in strict mode and must pass with no new ignores.
- **Style.** Code is formatted with `ruff format` and linted with `ruff`
  (`E, F, I, B, UP, D` with the NumPy docstring convention). Public symbols carry
  NumPy-style docstrings.
- **Licensing.** Every new file carries the SPDX header block (see any existing
  file); `make reuse` must stay clean.
- **Single responsibility.** One module is one responsibility; split rather than
  grow a module that does two things.
- **No fabricated data.** Every benchmark number in the docs comes from a
  committed, runnable script under `benchmarks/` with results checked in.

## Pull requests

1. Branch from `main`.
2. Make the change with tests and docs in the same commit sequence.
3. Run `make preflight` until it is green.
4. Open a pull request describing the change, the rationale, and how it was
   verified. Link any related issue.

The wire protocol and coordination model are described in
[`ARCHITECTURE.md`](ARCHITECTURE.md) and [`TEAM_PROTOCOL.md`](TEAM_PROTOCOL.md);
read those before changing message types or the hub state machine.
