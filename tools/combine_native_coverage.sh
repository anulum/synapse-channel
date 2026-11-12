#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — combine native subprocess observations assigned to this shard
set -euo pipefail

data_file=${COVERAGE_FILE:-.coverage}
test -s "$data_file"
test ! -L "$data_file"
test -d native-kernel-coverage
test ! -L native-kernel-coverage
tool_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python - "$tool_root" <<'PY'
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from coverage import CoverageData

sys.path.insert(0, sys.argv[1])
from tools.pytest_native_coverage import NATIVE_WRITERS, PROBE_TEST_PREFIX

receipt = json.loads(Path('shard-main.json').read_text())
selected = receipt['selected']
if receipt['success'] is not True or not isinstance(selected, list) or any(
    not isinstance(node, str) for node in selected
):
    raise ValueError('missing successful native coverage selection receipt')
root = Path('native-kernel-coverage')
cases = {
    node: root / hashlib.sha256(node.encode('utf-8')).hexdigest()
    for node in selected if node.split('[', 1)[0] in NATIVE_WRITERS
}
if set(root.iterdir()) != set(cases.values()):
    raise ValueError('missing or unassigned native coverage case directories')
for node, directory in cases.items():
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('invalid native coverage case directory: ' + node)
    traces = list(directory.glob('.coverage.*'))
    if not traces:
        raise ValueError('missing native subprocess coverage for case: ' + node)
    for trace in traces:
        if trace.is_symlink() or not trace.is_file():
            raise ValueError('invalid native coverage file: ' + node)
        data = CoverageData(basename=str(trace))
        data.read()
        if not data.has_arcs() or not data.measured_files():
            raise ValueError('missing native branch observations: ' + node)
probe_root = root.with_name(root.name + '-probe')
probe_traces = []
if probe_root.exists() or probe_root.is_symlink():
    if probe_root.is_symlink() or not probe_root.is_dir():
        raise ValueError('invalid probe coverage root')
    assigned = {
        probe_root / hashlib.sha256(node.encode('utf-8')).hexdigest()
        for node in selected if node.startswith(PROBE_TEST_PREFIX)
    }
    if not set(probe_root.iterdir()).issubset(assigned):
        raise ValueError('unassigned probe coverage case directory')
    for case in probe_root.iterdir():
        if case.is_symlink() or not case.is_dir():
            raise ValueError('invalid probe coverage case directory')
        for entry in case.rglob('*'):
            if entry.is_symlink() or not (entry.is_file() or entry.is_dir()):
                raise ValueError('invalid probe coverage entry')
            if entry.is_file() and entry.name.startswith('.coverage'):
                data = CoverageData(basename=str(entry))
                data.read()
                if not data.has_arcs() or not data.measured_files():
                    raise ValueError('missing probe branch observations')
                probe_traces.append(entry)
if cases:
    subprocess.run(
        [sys.executable, '-m', 'coverage', 'combine', '--append',
         *(str(directory) for directory in cases.values())], check=True,
    )
if probe_traces:
    subprocess.run(
        [sys.executable, '-m', 'coverage', 'combine', '--append', '--keep',
         *(str(trace) for trace in probe_traces)], check=True,
    )
PY
