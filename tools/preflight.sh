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
run "version sync"         "$V/python" tools/check_version_sync.py
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
