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

   A commit that stages Python or `pyproject.toml` runs strict mypy across the
   whole configured tree. Expect this correctness gate to add tens of seconds;
   it deliberately ignores staged-file narrowing. The hook prefers the
   repository `.venv` on POSIX or Windows. If the dev environment lives
   elsewhere, set `SYNAPSE_MYPY_PYTHON` to its absolute Python path; an invalid
   override fails closed.

   The installed pre-push hooks are deliberately lightweight. They check the
   generated capability snapshot, commit-trailer history, and version surfaces
   in seconds. They do not run pytest, coverage, or `tools/preflight.sh`; CI owns
   exhaustive tests for ordinary pushes. Run the exhaustive script locally only
   when the current task explicitly reserves resources for it or for an
   owner-authorised release verification.

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
- **Coverage exclusions are ledgered.** Every `pragma: no cover` in `src/` and
  every conditional skip in `tests/` is enumerated with a justification class
  in `tests/test_coverage_exclusion_ledger.py`; the suite fails the moment the
  tree and the ledger disagree, so adding one is a deliberate, reviewed edit.
  Unconditional skips and xfails are not accepted.
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
- **Changelog.** A user-visible change adds a `CHANGELOG.md` `[Unreleased]`
  fragment under the right heading (`Added`, `Changed`, `Fixed`, `Security`).
- **Backward compatibility.** A change to the wire protocol, a CLI flag's meaning,
  or a public function's signature states its compatibility impact in the commit
  or PR. Until 1.0 the wire format may still change; say so when it does.
- **Threat-model delta.** A change that touches authentication, ACLs, exposure
  guards, TLS, rate limiting, or the durable log states what it changes in the
  posture and updates [`SECURITY.md`](SECURITY.md) or
  [`docs/paranoid-mode.md`](docs/paranoid-mode.md) when a control's contract moves.

### Changes to `core/*`

The `core/` modules sit on the request, authentication, and durability hot paths,
so a change there carries extra weight. State in the commit or PR which invariant
the change preserves — no overlapping claims, no ACL bypass, no unauthenticated
mutation, replay idempotency, no lost durability — and add the test that pins it.
When a hot-path module (`hub.py`, `state.py`, `message_auth.py`, `persistence.py`)
grows a second responsibility, prefer extracting a new module over widening it.

## Pull requests

1. Branch from `main`.
2. Make the change with tests and docs in the same commit sequence.
3. Run `make preflight` until it is green.
4. Open a pull request describing the change, the rationale, and how it was
   verified. Link any related issue.

The wire protocol and coordination model are described in
[`ARCHITECTURE.md`](ARCHITECTURE.md) and [`TEAM_PROTOCOL.md`](TEAM_PROTOCOL.md);
read those before changing message types or the hub state machine.
