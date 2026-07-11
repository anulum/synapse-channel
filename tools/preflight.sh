#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — pre-push gate that mirrors CI; a green run here means CI is green.
#
# Run this before EVERY push: `bash tools/preflight.sh`. It runs the exact checks the
# CI workflows gate on, so a local pass guarantees a green CI. The ONE residual gap is
# the test matrix: CI runs pytest on 3.10–3.13, this runs only the local interpreter.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
V=.venv/bin
fails=()

run() {
  local name="$1"
  shift
  printf '\n== %s ==\n' "$name"
  if "$@"; then
    echo "  ok"
  else
    echo "  FAIL"
    fails+=("$name")
  fi
}

# ci.yml: lint
run "ruff format"          "$V/ruff" format --check src tests benchmarks examples
run "ruff lint"            "$V/ruff" check src tests benchmarks examples
run "capability manifest"  "$V/python" tools/capability_manifest.py --check
run "dev dependency drift" "$V/python" tools/check_dev_dependency_drift.py --check
run "dependency/tooling audit" "$V/python" tools/audit_dependency_tooling.py --check
run "cockpit CI contract"  "$V/python" tools/check_cockpit_ci.py --check
run "version sync"         "$V/python" tools/check_version_sync.py
run "MCP surface audit"    "$V/python" tools/audit_mcp_surface.py --check
run "release claim hygiene" "$V/python" tools/check_release_claim_hygiene.py --check
run "commercial claim hygiene" "$V/python" tools/check_commercial_claim_hygiene.py --check
run "commit trailer history" "$V/python" tools/check_commit_trailers.py
run "bandit tooling audit" "$V/python" -m bandit -q tools/audit_dependency_tooling.py \
  tools/check_dev_dependency_drift.py
# ci.yml: typecheck
run "mypy (strict)"        "$V/mypy"
# ci.yml: test (single-version; CI runs the 3.10-3.13 matrix)
run "pytest + coverage"    "$V/python" -m pytest --cov=synapse_channel \
  --cov-report=term-missing --cov-report=xml -q
# ci.yml: reuse
run "reuse lint"           "$V/python" -m reuse lint
# ci.yml: audit (identical flags to .github/workflows/ci.yml)
run "pip-audit"            "$V/python" -m pip_audit --skip-editable --desc --progress-spinner=off
# docs.yml
run "mkdocs (strict)"      "$V/python" -m mkdocs build --strict
rm -rf site coverage.xml

# pre-commit.yml is a SEPARATE push gate, not part of ci.yml. Its ruff/format hooks are
# already covered above; the remaining gap is the typos spell-check, mirrored here so a
# green preflight implies a green pre-commit run too (typos splits on hyphens, so a
# legitimate hyphenated prefix can trip it — catch it locally, not in CI).
if command -v typos >/dev/null 2>&1; then
  run "typos"              typos --config _typos.toml
else
  printf '\n== typos ==\n  FAIL — typos not installed; run `cargo install typos-cli` (mirrors the pre-commit gate)\n'
  fails+=("typos")
fi

# scorecard.yml: every action must be pinned to a full commit SHA (PinnedDependencies).
printf '\n== action pinning ==\n'
if grep -rEn 'uses: [^@]+@v[0-9]' .github/workflows/ >/dev/null 2>&1; then
  echo "  FAIL — these actions are pinned to a tag, not a SHA:"
  grep -rEn 'uses: [^@]+@v[0-9]' .github/workflows/ | sed 's/^/    /'
  fails+=("action pinning")
else
  echo "  ok"
fi

echo
if [ ${#fails[@]} -ne 0 ]; then
  printf 'PREFLIGHT FAILED (%d): %s\nDO NOT PUSH.\n' "${#fails[@]}" "${fails[*]}"
  exit 1
fi
printf 'PREFLIGHT GREEN — safe to push.\n'
