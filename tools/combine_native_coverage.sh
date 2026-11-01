#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — combine native subprocess observations assigned to this shard
set -euo pipefail

test -s .coverage
test ! -L .coverage
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
from tools.pytest_native_coverage import NATIVE_WRITERS

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
if cases:
    subprocess.run(
        [sys.executable, '-m', 'coverage', 'combine', '--append',
         *(str(directory) for directory in cases.values())], check=True,
    )
PY
