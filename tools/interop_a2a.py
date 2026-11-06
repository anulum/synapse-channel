# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the official a2a-sdk client against a real Synapse A2A bridge
"""Run the a2a-sdk journey: hub and bridge here, the client in its own environment.

The hub and ``synapse a2a-serve`` run from this interpreter. The client half,
``tools/interop_a2a_client.py``, runs under the interpreter of a separate
environment that holds ``a2a-sdk``, and reports its steps as JSON. The journey
is then judged by the hub's journal. The bridge must have forwarded the message
as a chat from its own identity to its target, carrying the A2A task id the
client saw.
"""

from __future__ import annotations

import secrets
import subprocess  # nosec B404
from pathlib import Path

from tools.interop_hub import (
    Provisioned,
    clean_environment,
    free_port,
    hub_command,
    provision,
    running,
    synapse_command,
)
from tools.interop_journey import Steps, journey_record, judge, read_client_report
from tools.interop_oracle import JournalOracle
from tools.interop_record import JourneyRecord

CLIENT = "a2a-sdk"
JOURNEY = "card-send-get-list-cancel"
TRANSPORT = "http-json"
BRIDGE = "interop/a2a-bridge"
RECEIVER = "interop/receiver"
MARKER = "synapse-interop-a2a-marker"
FIRST_JOURNAL_STEP = "journal-chat"
_CLIENT_SCRIPT = Path(__file__).with_name("interop_a2a_client.py")


def _bearer(provisioned: Provisioned, name: str, value: str) -> Path:
    path = provisioned.directory / name
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def bridge_command(provisioned: Provisioned, hub_uri: str, port: int, bearer: Path) -> list[str]:
    """Return the A2A bridge command: bearer-protected, forwarding to one receiver."""
    return synapse_command(
        "a2a-serve",
        "--uri",
        hub_uri,
        "--name",
        BRIDGE,
        "--token-file",
        str(provisioned.hub_token_file),
        "--port",
        str(port),
        "--endpoint-url",
        f"http://127.0.0.1:{port}",
        "--target",
        RECEIVER,
        "--bearer-auth",
        "--a2a-token-file",
        str(bearer),
    )


def _client_half(client_python: Path, url: str, bearer: Path, steps: Steps) -> tuple[str, str, str]:
    """Run the client script; return ``(failed_step, client_version, task_id)``."""
    # A fixed command: the client environment's interpreter and the client script.
    completed = subprocess.run(  # noqa: S603  # nosec B603
        [str(client_python), "-I", str(_CLIENT_SCRIPT), url, str(bearer), MARKER],
        capture_output=True,
        text=True,
        timeout=120,
        env=clean_environment(),
        check=False,
    )
    report = read_client_report(completed.stdout, completed.returncode, steps)
    if report is None:
        return "client-run", "", ""
    return str(report["failed_step"]), str(report["client_version"]), str(report["task_id"])


def run_journey(
    control: str, directory: Path, *, client_python: Path
) -> tuple[JourneyRecord, list[str]]:
    """Run the A2A journey in ``control`` mode; return its record and the run's secrets."""
    provisioned = provision(directory / "provision")
    bearer_value = secrets.token_urlsafe(32)
    bridge_bearer = _bearer(provisioned, "a2a.bearer", bearer_value)
    client_bearer = bridge_bearer
    if control == "surface-disabled":
        # The client presents a bearer the bridge does not know.
        client_bearer = _bearer(provisioned, "wrong.bearer", secrets.token_urlsafe(32))
    db = directory / "hub.db"
    hub_port, bridge_port = free_port(), free_port()
    steps = Steps()
    task_id = ""
    version = ""
    with running(
        hub_command(provisioned, db, hub_port, identity_binding=False),
        port=hub_port,
        label="hub",
        log=directory / "hub.log",
    ):
        with running(
            bridge_command(provisioned, f"ws://127.0.0.1:{hub_port}", bridge_port, bridge_bearer),
            port=bridge_port,
            label="A2A bridge",
            log=directory / "a2a.log",
        ):
            failed_step, version, task_id = _client_half(
                client_python, f"http://127.0.0.1:{bridge_port}", client_bearer, steps
            )
    journal_sha256 = ""
    if not failed_step:

        def check(oracle: JournalOracle) -> None:
            oracle.chat_recorded(BRIDGE, RECEIVER, MARKER, a2a_task_id=task_id)

        failed_step, journal_sha256 = judge(
            steps, db, control=control, unrelated=directory / "unrelated.db", assertions=check
        )
    record = journey_record(
        client=CLIENT,
        version=version,
        integrity="",
        transport=TRANSPORT,
        journey=JOURNEY,
        steps=steps,
        failed_step=failed_step,
        journal_sha256=journal_sha256,
    )
    secret_values = [
        *provisioned.secrets(),
        bearer_value,
        client_bearer.read_text(encoding="utf-8").strip(),
    ]
    return record, secret_values
