# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — enrolling this machine's identity key for an identity-bound hub
"""``synapse identity machine-key`` enrols the key every client already presents.

The session conftest points ``$XDG_DATA_HOME`` at a temporary directory, so the
machine key here is a real provisioned key that the real client also signs with.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel import cli, cli_identity, cli_queries
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.identity_binding import load_identity_trust_bundle
from synapse_channel.machine_identity import ensure_machine_identity


def _run(argv: list[str]) -> int:
    args = cli.build_parser().parse_args(argv)
    return int(args.func(args))


def test_machine_key_parser_needs_at_least_one_sender() -> None:
    """The entry is useless without a name it proves, so ``--sender`` is required."""
    args = cli.build_parser().parse_args(["identity", "machine-key", "--sender", "P/a"])
    assert args.func is cli_identity._cmd_identity_machine_key
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["identity", "machine-key"])


def test_machine_key_prints_the_entry_for_the_key_clients_present(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The printed entry names the machine key and every sender once, in order."""
    code = _run(
        [
            "identity",
            "machine-key",
            "--sender",
            "P/seat",
            "--sender",
            "P/seat-rx",
            "--sender",
            "P/seat",
            "--expires-at",
            "1900000000",
        ]
    )
    out = capsys.readouterr().out
    machine = ensure_machine_identity()
    entry = json.loads(out[out.index("{") :])["keys"][0]
    assert code == 0
    assert f"machine key {machine.key_id}" in out
    assert entry == {
        "key_id": machine.key_id,
        "public_key": machine.public_key,
        "senders": ["P/seat", "P/seat-rx"],
        "expires_at": 1900000000.0,
    }


def test_machine_key_entry_has_no_expiry_unless_asked(capsys: pytest.CaptureFixture[str]) -> None:
    """Without ``--expires-at`` the entry carries no expiry field at all."""
    assert _run(["identity", "machine-key", "--sender", "P/seat"]) == 0
    out = capsys.readouterr().out
    assert "expires_at" not in json.loads(out[out.index("{") :])["keys"][0]


async def test_an_enrolled_machine_key_admits_its_names_on_an_identity_bound_hub(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """After enrolment the default client is admitted as P/seat and refused as P/other."""
    trust = tmp_path / "identity-trust.json"
    code = cli_identity._cmd_identity_machine_key(
        cli.build_parser().parse_args(
            ["identity", "machine-key", "--sender", "P/seat", "--trust", str(trust)]
        )
    )
    assert code == 0
    assert "enrolled machine key machine-" in capsys.readouterr().out
    hub = SynapseHub(
        authenticator=TokenAuthenticator(["seat-token"]),
        identity_trust_bundle=load_identity_trust_bundle(trust),
        require_identity_binding=True,
    )
    async with running_hub(hub) as (_, uri):
        admitted = await cli_queries._health(uri=uri, name="P/seat", token="seat-token")
        refused = await cli_queries._health(
            uri=uri, name="P/other", token="seat-token", ready_timeout=1.0
        )
    assert (admitted, refused) == (0, 1)


def test_machine_key_refuses_a_second_enrolment_in_the_same_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A key is enrolled once; a repeat is an error, never a shadowing second entry."""
    trust = tmp_path / "identity-trust.json"
    argv = ["identity", "machine-key", "--sender", "P/seat", "--trust", str(trust)]
    assert _run(argv) == 0
    capsys.readouterr()
    assert _run(argv) == 2
    assert "identity machine-key error" in capsys.readouterr().out
    assert len(json.loads(trust.read_text(encoding="utf-8"))["keys"]) == 1
