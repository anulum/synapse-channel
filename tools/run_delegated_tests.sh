#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native pytest process birthplace inside user delegation
set -euo pipefail

if (( $# == 0 )); then
    echo 'usage: run_delegated_tests.sh command [argument ...]' >&2
    exit 2
fi
if [[ $(id -u) == 0 ]]; then
    echo 'native tests require the actual unprivileged runner account' >&2
    exit 2
fi
read -r unique < /proc/sys/kernel/random/uuid
unit="synapse-ci-pytest-${unique}.service"
load_state=$(systemctl --user show "$unit" --property=LoadState --value 2>/dev/null || true)
if [[ "$load_state" != not-found ]]; then
    echo 'cannot prove the reserved transient test unit is absent' >&2
    exit 2
fi

cleanup() {
    local original_exit=$1 cleanup_exit=0 observed
    trap - EXIT HUP INT TERM
    observed=$(systemctl --user show "$unit" --property=LoadState --value 2>/dev/null || true)
    if [[ "$observed" == loaded ]]; then
        systemctl --user stop "$unit" || cleanup_exit=$?
    elif [[ "$observed" != not-found ]]; then
        echo "cannot verify transient test unit cleanup: $unit" >&2
        cleanup_exit=1
    fi
    if (( original_exit == 0 && cleanup_exit != 0 )); then
        exit "$cleanup_exit"
    fi
    exit "$original_exit"
}
trap 'cleanup "$?"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Pass only the job inputs these tests consume. Provider secrets and GitHub tokens stay out.
environment=()
environment_names=()
for key in PATH HOME LANG LC_ALL LD_LIBRARY_PATH PYTHONPATH PYTHONDONTWRITEBYTECODE \
    PYTHONUNBUFFERED PYTEST_DISABLE_PLUGIN_AUTOLOAD PYTEST_ADDOPTS PYTEST_PLUGINS \
    COVERAGE_FILE COVERAGE_RCFILE COVERAGE_PROCESS_START COVERAGE_CORE \
    COV_CORE_SOURCE COV_CORE_CONFIG COV_CORE_DATAFILE PIP_CONFIG_FILE PIP_CACHE_DIR \
    PIP_NO_INDEX PIP_DISABLE_PIP_VERSION_CHECK SHARD_INDEX SHARD_COUNT PYTHON_PROFILE \
    TMPDIR TMP TEMP XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS \
    CI GITHUB_ACTIONS GITHUB_WORKSPACE GITHUB_SHA GITHUB_RUN_ID GITHUB_RUN_ATTEMPT \
    RUNNER_TEMP RUNNER_TRACKING_ID SYNAPSE_TEST_PRE_COMMIT_BIN \
    SYNAPSE_TEST_FOREIGN_OWNER_FILE SYNAPSE_TEST_CODEX_BIN SYNAPSE_TEST_PI_BIN \
    OPENCODE_BIN DISABLE_TELEMETRY CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC; do
    if [[ -v "$key" ]]; then
        environment+=("${key}=${!key}")
        environment_names+=("$key")
    fi
done
printf 'Native test environment names: %s\n' "${environment_names[*]}"

# Birth inside user@ delegation makes the common-ancestor cgroup permission real.
# RuntimeMaxSec matches the existing six-hour GitHub Actions job limit.
launch_umask=$(umask)
original_exit=0
systemd-run --user --wait --pipe --quiet --same-dir --unit="$unit" \
    --property=Delegate=yes --property=KillMode=control-group --property=UMask="$launch_umask" \
    --property=TimeoutStopSec=15s --property=RuntimeMaxSec=6h \
    /usr/bin/env -i "${environment[@]}" "$@" || original_exit=$?
cleanup "$original_exit"
