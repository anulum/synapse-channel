<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — how to check the coverage figure yourself
-->

# Checking the coverage figure

The coverage gate requires 99.36 % line and branch coverage of
`src/synapse_channel` (`[tool.coverage.report]` in `pyproject.toml`). This page
shows how to read the measured figure from CI and how to measure it again.

## What is measured

- **Scope.** Every module under `src/synapse_channel`, with branch coverage
  on. Exclusions are limited to the patterns in `pyproject.toml`, and every
  `pragma: no cover` is listed with a reason in
  `tests/test_coverage_exclusion_ledger.py`.
- **Population.** On every push to main and every pull request, the `ci`
  workflow runs the whole test inventory in four shards on Python 3.11,
  3.12 and 3.13. The Python 3.12 shards record coverage, including
  subprocesses and the native Linux profiles listed in `CONTRIBUTING.md`.
- **Gate.** `tools/ci_shard_artifacts.py verify` checks that the shards
  together ran every collected test exactly once. It then combines their
  coverage and fails the run below 99.36 %. A shard has no floor of its own.

## Read the figure of a CI run

Each successful `ci` run keeps the combined report as the artifact
`python-coverage-<commit sha>`, holding `coverage.json` and `coverage.xml`.
With the GitHub CLI:

```bash
gh run list --repo anulum/synapse-channel --workflow ci --branch main --limit 1
gh run download <run id> --repo anulum/synapse-channel --name python-coverage-<commit sha>
python -c "import json; print(json.load(open('coverage.json'))['totals']['percent_covered'])"
```

`coverage.json` also lists every file with its missing lines and branches.
The same `coverage.xml` goes to Codecov, the badge in the README, but the
artifact is authoritative: Codecov upload failures do not fail the run.

## Measure it again

From a clone with the development environment of `CONTRIBUTING.md`:

```bash
make cov
```

This runs the default collection on one interpreter with the same
coverage configuration. It is the whole test suite, so it takes time and
resources. The figure can differ slightly from CI's: the native Linux
profiles need the host prerequisites named in `CONTRIBUTING.md`, and some
cases need prepared fixtures (`SYNAPSE_TEST_FOREIGN_OWNER_FILE`, the
released-hub wheels). Without them those tests fail rather than skip.
