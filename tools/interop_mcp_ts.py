# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the official MCP TypeScript SDK against real Synapse processes
"""Run the MCP TypeScript SDK row of the interop lane.

The Synapse processes start here exactly as for the Python SDK row. The client
half is ``clients/interop-mcp-ts``, built from its committed lock, which runs
under Node with its own ``node_modules``. It receives a configuration file
holding paths only; HTTP access tokens are passed in owner-only files, never on
a command line.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404
from pathlib import Path

from tools.interop_hub import (
    HTTP_SUBJECTS,
    RESOURCE,
    STDIO_NAMES,
    Provisioned,
    clean_environment,
    stdio_mcp_command,
)
from tools.interop_journey import Steps, read_client_report
from tools.interop_mcp_python import HTTP, MARKER, STDIO, run_mcp_row
from tools.interop_record import JourneyRecord, StepRecord

CLIENT = "mcp-typescript-sdk"
NODE = "node"
"""The Node executable that runs the built client."""
CLIENT_DIRECTORY = Path(__file__).resolve().parent.parent / "clients" / "interop-mcp-ts"
_SDK_LOCK_KEY = "node_modules/@modelcontextprotocol/sdk"


def sdk_pin(directory: Path = CLIENT_DIRECTORY) -> tuple[str, str]:
    """Return the locked SDK version and its integrity hash from the committed lock."""
    lock = json.loads((directory / "package-lock.json").read_text(encoding="utf-8"))
    entry = lock["packages"][_SDK_LOCK_KEY]
    return str(entry["version"]), str(entry["integrity"])


def _owner_only(path: Path, text: str) -> Path:
    path.write_text(text + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def configuration(
    provisioned: Provisioned, hub_uri: str, http_url: str | None, control: str
) -> dict[str, object]:
    """Return the client's configuration: paths and names, never a secret."""
    who = STDIO if http_url is None else HTTP
    config: dict[str, object] = {
        "transport": "stdio" if http_url is None else "streamable-http",
        "first": who.first,
        "second": who.second,
        "task_id": who.task_id,
        "operation_ids": who.operation_ids,
        "marker": MARKER,
    }
    if http_url is None:
        servers = []
        for name in STDIO_NAMES:
            command = stdio_mcp_command(provisioned, hub_uri, name)
            servers.append(
                {
                    "command": command[0],
                    "args": command[1:],
                    "cwd": str(provisioned.directory),
                    "env": clean_environment(),
                }
            )
        config["servers"] = servers
        return config
    audience = RESOURCE if control != "surface-disabled" else "https://other.interop.test/mcp"
    config["url"] = http_url
    config["token_files"] = [
        str(
            _owner_only(
                provisioned.directory / f"{subject}.access-token",
                provisioned.access_token(subject, audience=audience),
            )
        )
        for subject in HTTP_SUBJECTS
    ]
    return config


def typescript_client_half(
    provisioned: Provisioned, hub_uri: str, http_url: str | None, control: str, steps: Steps
) -> str:
    """Run the built TypeScript client and copy its steps into ``steps``."""
    config_file = provisioned.directory / "ts-client.json"
    config_file.write_text(
        json.dumps(configuration(provisioned, hub_uri, http_url, control)), encoding="utf-8"
    )
    environment = clean_environment()
    environment["NODE_EXTRA_CA_CERTS"] = str(provisioned.certificate)
    try:
        # A fixed command: the configured Node and the built client.
        completed = subprocess.run(  # noqa: S603  # nosec B603
            [NODE, str(CLIENT_DIRECTORY / "dist" / "journey.js"), str(config_file)],
            capture_output=True,
            text=True,
            timeout=120,
            env=environment,
            check=False,
            cwd=os.fspath(CLIENT_DIRECTORY),
        )
    except OSError:
        steps.records.append(StepRecord("client-run", "client process", "node-missing"))
        return "client-run"
    report = read_client_report(completed.stdout, completed.returncode, steps)
    if report is None:
        return "client-run"
    return str(report["failed_step"])


def run_journey(transport: str, control: str, directory: Path) -> tuple[JourneyRecord, list[str]]:
    """Run the TypeScript SDK row on ``transport`` in ``control`` mode."""
    version, integrity = sdk_pin()
    return run_mcp_row(
        transport,
        control,
        directory,
        client=CLIENT,
        version=version,
        integrity=integrity,
        client_half=typescript_client_half,
    )
