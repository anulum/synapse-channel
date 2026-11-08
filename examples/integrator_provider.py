# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the provider process of the minimal integrator adapter
"""Declare the operation, then make its effect: steps 2 and 4 of the integrator contract.

``synapse worker-session`` starts this process with ``SYN_OPERATION_ID`` set to the
operation id of its outcome report. Before touching anything the provider declares
that operation on the claimed task (``synapse operation declare``); only an accepted
declaration lets it write the file. Exit codes: 0 written, 4 the hub did not accept
the declaration, 5 ``SYN_OPERATION_ID`` is missing.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

from synapse_channel.cli import main as synapse
from synapse_channel.worker_outcome import OPERATION_ID_ENV


def run(argv: list[str]) -> int:
    """Declare the operation of this launch and write the requested file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--epoch", required=True)
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--text", required=True)
    args = parser.parse_args(argv)
    if not os.environ.get(OPERATION_ID_ENV):
        return 5
    data = args.text.encode("utf-8")
    declared = synapse(
        [
            "operation",
            "declare",
            "--task",
            args.task,
            "--name",
            args.identity,
            "--uri",
            args.uri,
            "--epoch",
            args.epoch,
            "--effect",
            "file",
            "--path",
            str(args.path),
            "--sha256",
            hashlib.sha256(data).hexdigest(),
        ]
    )
    if declared != 0:
        return 4
    args.path.write_bytes(data)
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
