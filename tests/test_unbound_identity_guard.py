# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exposed or multi-seat hubs refuse to start without provisioned identity
"""K4-F2 / SOL4-ID-01: identity is provisioned wherever more than one owner can reach the hub.

The pure decision is tested directly; the start-up behaviour goes through
``_cmd_hub`` with the real argument namespace, so the refusal, the explicit
downgrade and the no-database-on-refusal guarantee are all exercised.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cli_processes_helpers import _hub_ns
from cli_processes_hub_helpers import _close_runner, _write_identity_trust
from synapse_channel import cli, cli_processes
from synapse_channel.core.unbound_identity_guard import (
    UnboundIdentityError,
    refusal_message,
    unbound_identity_problem,
)

# Off loopback the plaintext-token and plaintext-store refusals fire first; these
# opt-outs isolate the identity decision under test.
_EXPOSED: dict[str, Any] = {
    "host": "0.0.0.0",
    "token": "shared",
    "insecure_off_loopback": True,
    "insecure_plaintext_at_rest": True,
}


def test_a_single_owner_loopback_hub_needs_no_identity() -> None:
    assert (
        unbound_identity_problem("127.0.0.1", declared_multi_seat=False, identity_bound=False)
        is None
    )


@pytest.mark.parametrize(
    ("host", "multi_seat", "fragment"),
    [
        ("0.0.0.0", False, "binds off-loopback host '0.0.0.0'"),
        ("100.64.0.7", True, "binds off-loopback host '100.64.0.7'"),
        ("localhost", True, "declares a multi-seat profile"),
    ],
)
def test_exposure_or_a_multi_seat_declaration_needs_identity(
    host: str, multi_seat: bool, fragment: str
) -> None:
    problem = unbound_identity_problem(host, declared_multi_seat=multi_seat, identity_bound=False)
    assert problem is not None
    assert fragment in problem
    assert "--identity-trust FILE with --require-identity-binding" in problem
    assert (
        unbound_identity_problem(host, declared_multi_seat=multi_seat, identity_bound=True) is None
    )


def test_the_refusal_names_the_explicit_downgrade_and_the_error_code() -> None:
    message = refusal_message("declares a multi-seat profile")
    assert message.startswith("Refusing to start: Synapse Hub declares a multi-seat profile")
    assert "--insecure-unbound-identity" in message
    assert UnboundIdentityError.code == "unbound_identity"


def test_an_exposed_hub_without_identity_refuses_before_opening_its_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "hub.db"
    ns = _hub_ns(db=str(db_path), **_EXPOSED)
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    err = capsys.readouterr().err
    assert "Refusing to start: Synapse Hub binds off-loopback host '0.0.0.0'" in err
    assert not db_path.exists()


@pytest.mark.parametrize(
    "declaration",
    [
        {"expect_multi_seat": True},
        {"bridge_exposed": True},
        {"private_directed_messages": True},
        {"require_role_claim": True},
    ],
)
def test_a_declared_multi_seat_loopback_hub_without_identity_refuses(
    declaration: dict[str, bool], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_processes._cmd_hub(_hub_ns(**declaration), runner=_close_runner) == 2
    assert "declares a multi-seat profile" in capsys.readouterr().err


def test_the_explicit_downgrade_starts_and_warns(capsys: pytest.CaptureFixture[str]) -> None:
    ns = _hub_ns(insecure_unbound_identity=True, **_EXPOSED)
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 0
    err = capsys.readouterr().err
    assert "WARNING Synapse Hub binds off-loopback host '0.0.0.0' without provisioned" in err
    assert "Refusing" not in err


def test_provisioned_identity_starts_an_exposed_hub_without_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust = tmp_path / "identity-trust.json"
    _write_identity_trust(trust)
    ns = _hub_ns(identity_trust=str(trust), require_identity_binding=True, **_EXPOSED)
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 0
    assert "provisioned identity" not in capsys.readouterr().err


def test_the_downgrade_flag_is_a_real_hub_option() -> None:
    args = cli.build_parser().parse_args(["hub", "--insecure-unbound-identity"])
    assert args.insecure_unbound_identity is True
    assert cli.build_parser().parse_args(["hub"]).insecure_unbound_identity is False
