# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the read-only hub query commands (who/state/board/manifest/health)

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from hub_e2e_helpers import _free_port, running_hub
from synapse_channel import cli_queries
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.identity_binding import enroll_identity_key, load_identity_trust_bundle
from synapse_channel.core.identity_keys import (
    generate_signing_key,
    public_key_b64,
    write_signing_key,
)


async def test_health_ok_when_ready() -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        code = await cli_queries._health(uri=uri, name="H")
    assert code == 0


async def test_health_fail_when_unreachable() -> None:
    code = await cli_queries._health(
        uri=f"ws://127.0.0.1:{_free_port()}", name="H", ready_timeout=0.1
    )
    assert code == 1


async def test_drop_message_is_noop() -> None:
    await cli_queries._drop_message({"type": "x"})  # a no-op callback; must simply not raise


def test_cmd_health_dispatches_real_probe() -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}", name="H", token=None, ready_timeout=0.1
    )
    assert cli_queries._cmd_health(ns) == 1


def _enrolled_probe_key(tmp_path: Path, *, sender: str = "HEALTH") -> tuple[Path, Path]:
    """Generate a probe key and enrol it for ``sender`` the way ``identity keygen`` does."""
    key = generate_signing_key()
    key_path = tmp_path / "health.pem"
    write_signing_key(key_path, key)
    trust = tmp_path / "identity-trust.json"
    enroll_identity_key(
        trust, key_id="health-1", public_key_b64=public_key_b64(key), senders=[sender]
    )
    return key_path, trust


async def test_health_signs_its_registration_for_an_identity_bound_hub(tmp_path: Path) -> None:
    """With an enrolled key the probe is admitted; without one the bound hub refuses it.

    A token-guarded hub withholds its welcome until the registration binds a name, so
    a probe the identity gate refuses never sees the welcome and reports unhealthy.
    """
    key_path, trust = _enrolled_probe_key(tmp_path)
    hub = SynapseHub(
        authenticator=TokenAuthenticator(["probe-token"]),
        identity_trust_bundle=load_identity_trust_bundle(trust),
        require_identity_binding=True,
    )
    async with running_hub(hub) as (_, uri):
        signed = await cli_queries._health(
            uri=uri,
            token="probe-token",
            identity_key_path=str(key_path),
            identity_key_id="health-1",
        )
        unsigned = await cli_queries._health(uri=uri, token="probe-token", ready_timeout=1.0)
        wrong_name = await cli_queries._health(
            uri=uri,
            name="OTHER",
            token="probe-token",
            identity_key_path=str(key_path),
            identity_key_id="health-1",
            ready_timeout=1.0,
        )
    assert (signed, unsigned, wrong_name) == (0, 1, 1)


def _health_args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "uri": f"ws://127.0.0.1:{_free_port()}",
        "name": "HEALTH",
        "token": None,
        "ready_timeout": 0.1,
        "identity_key_file": None,
        "identity_key_id": "",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.mark.parametrize(
    "overrides",
    [{"identity_key_file": "health.pem"}, {"identity_key_id": "health-1"}],
)
def test_cmd_health_refuses_half_an_identity(
    overrides: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """A key file without its id, or an id without a file, is a configuration error."""
    assert cli_queries._cmd_health(_health_args(**overrides)) == 2
    assert "must be given together" in capsys.readouterr().err


def test_cmd_health_reports_an_unreadable_key_as_a_configuration_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing key file exits 2 with the reason, not a traceback or a false unhealthy."""
    args = _health_args(identity_key_file=str(tmp_path / "absent.pem"), identity_key_id="k")
    assert cli_queries._cmd_health(args) == 2
    assert "cannot read identity key" in capsys.readouterr().err


def test_cmd_health_passes_a_valid_key_to_the_probe(tmp_path: Path) -> None:
    """A loadable key reaches the probe; an unreachable hub is still just unhealthy."""
    key_path, _trust = _enrolled_probe_key(tmp_path)
    args = _health_args(identity_key_file=str(key_path), identity_key_id="health-1")
    assert cli_queries._cmd_health(args) == 1
