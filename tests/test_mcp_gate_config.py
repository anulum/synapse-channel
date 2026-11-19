# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — tests for the MCP bearer gate's configuration, bearers and seat tokens
"""The gate's configuration is strict, bearers are digests only, and seat tokens are checked."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mcp_gate_helpers import owner_file, seat_token
from synapse_channel.mcp.gate_config import (
    BEARER_LIFETIME,
    BEARER_SCHEMA,
    GATE_CONFIG_SCHEMA,
    GATE_DENIED_TOOLS,
    GateConfigError,
    SeatTokenUnavailable,
    bearer_digest,
    check_bearers,
    check_seat_grants,
    load_bearer,
    load_gate_config,
    new_bearer,
    read_seat_token,
)

PIN = "a" * 64
NOW = datetime(2026, 11, 19, 3, 0, tzinfo=UTC)
MESSAGING_TOOLS = [
    "synapse_board",
    "synapse_state",
    "synapse_manifest",
    "synapse_directory",
    "synapse_status",
    "synapse_send",
]


def grant_file(path: Path, seats: dict[str, list[str]]) -> Path:
    """Write a valid listener grant file granting each seat the listed tools."""
    key = Ed25519PrivateKey.generate().public_key()
    pem = key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")
    subjects = {
        f"subject-{index}": {
            "projects": {
                seat.split("/")[0]: {
                    "seat": seat,
                    "identity_key_file": f"/keys/{index}.key",
                    "identity_key_id": f"key-{index}",
                    "task_prefix": seat.split("/")[0] + "/",
                    "tools": tools,
                }
            }
        }
        for index, (seat, tools) in enumerate(seats.items())
    }
    document = {
        "issuer": "https://issuer.example",
        "resource": "https://gate.example/mcp",
        "public_keys": {"k1": pem},
        "subjects": subjects,
    }
    return owner_file(path, json.dumps(document))


def config_document(tmp_path: Path, **changes: Any) -> dict[str, Any]:
    """Return a valid configuration document with ``changes`` applied."""
    document: dict[str, Any] = {
        "schema": GATE_CONFIG_SCHEMA,
        "listen": {"host": "127.0.0.1", "port": 8889},
        "upstream": "https://127.0.0.1:8888",
        "upstream_cert_sha256": PIN,
        "public_host": "gate.example",
        "tls_cert_file": str(tmp_path / "gate.crt"),
        "tls_key_file": str(tmp_path / "gate.key"),
        "grant_file": str(tmp_path / "grants.json"),
        "seats": [
            {
                "seat": "PROJ/bot",
                "bearer_file": str(tmp_path / "bot.bearer"),
                "token_file": str(tmp_path / "bot.token"),
            }
        ],
    }
    document.update(changes)
    return document


def write_config(tmp_path: Path, **changes: Any) -> Path:
    """Write a configuration document as an owner-only file."""
    return owner_file(tmp_path / "gate.json", json.dumps(config_document(tmp_path, **changes)))


# --- the configuration ---------------------------------------------------------------------


def test_a_valid_configuration_loads(tmp_path: Path) -> None:
    config = load_gate_config(write_config(tmp_path, listen={"host": "localhost", "port": 9000}))
    assert (config.listen_host, config.listen_port) == ("127.0.0.1", 9000)
    assert (config.upstream_host, config.upstream_port) == ("127.0.0.1", 8888)
    assert config.upstream_cert_sha256 == PIN
    assert config.seats[0].seat == "PROJ/bot"
    assert config.seats[0].token_file == tmp_path / "bot.token"
    ipv6 = load_gate_config(write_config(tmp_path, upstream="https://[::1]:8888/"))
    assert ipv6.upstream_host == "::1"


def _seat(tmp_path: Path, name: str = "PROJ/bot", tag: str = "bot") -> dict[str, str]:
    return {
        "seat": name,
        "bearer_file": str(tmp_path / f"{tag}.bearer"),
        "token_file": str(tmp_path / f"{tag}.token"),
    }


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema": "synapse-mcp-gate.v0"}, "schema"),
        ({"extra": 1}, "schema"),
        ({"upstream_cert_sha256": "A" * 64}, "upstream_cert_sha256"),
        ({"upstream_cert_sha256": None}, "upstream_cert_sha256"),
        ({"public_host": "gate example"}, "public_host"),
        ({"public_host": 5}, "public_host"),
        ({"listen": {"host": "0.0.0.0", "port": 1}}, "listen.host must be a loopback"),
        ({"listen": {"host": "gate.example", "port": 1}}, "listen.host must be a loopback"),
        ({"listen": {"host": 2130706433, "port": 1}}, "listen.host must be a loopback"),
        ({"listen": {"host": "127.0.0.1", "port": 0}}, "listen.port"),
        ({"listen": {"host": "127.0.0.1", "port": True}}, "listen.port"),
        ({"listen": {"host": "127.0.0.1"}}, "listen must be"),
        ({"listen": "127.0.0.1:1"}, "listen must be"),
        ({"upstream": "http://127.0.0.1:8888"}, "upstream must be"),
        ({"upstream": "https://127.0.0.1:8888/mcp"}, "upstream must be"),
        ({"upstream": "https://127.0.0.1:8888?x=1"}, "upstream must be"),
        ({"upstream": "https://127.0.0.1:8888#x"}, "upstream must be"),
        ({"upstream": "https://u:p@127.0.0.1:8888"}, "upstream must be"),
        ({"upstream": 8888}, "upstream must be"),
        ({"upstream": "https://10.0.0.1:8888"}, "upstream host must be a loopback"),
        ({"upstream": "https://127.0.0.1"}, "upstream port"),
        ({"upstream": "https://127.0.0.1:99999"}, "upstream port"),
        ({"tls_cert_file": "gate.crt"}, "tls_cert_file must be an absolute path"),
        ({"grant_file": ""}, "grant_file must be an absolute path"),
        ({"seats": []}, "seats must list"),
        ({"seats": "PROJ/bot"}, "seats must list"),
        ({"seats": [{"seat": "PROJ/bot"}]}, "must be {seat, bearer_file, token_file}"),
        ({"seats": ["PROJ/bot"]}, "must be {seat, bearer_file, token_file}"),
        (
            {"seats": [{"seat": "bot", "bearer_file": "/b", "token_file": "/t"}]},
            "PROJECT/seat identity",
        ),
    ],
)
def test_a_configuration_outside_the_documented_shape_is_refused(
    tmp_path: Path, changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(GateConfigError, match=message):
        load_gate_config(write_config(tmp_path, **changes))


def test_a_seat_or_a_file_may_appear_only_once(tmp_path: Path) -> None:
    twice = [_seat(tmp_path), _seat(tmp_path, tag="other")]
    twice[1]["seat"] = "PROJ/bot"
    with pytest.raises(GateConfigError, match="appear once"):
        load_gate_config(write_config(tmp_path, seats=twice))
    shared = [_seat(tmp_path), _seat(tmp_path, name="PROJ/two", tag="two")]
    shared[1]["token_file"] = shared[0]["bearer_file"]
    with pytest.raises(GateConfigError, match="appear once"):
        load_gate_config(write_config(tmp_path, seats=shared))
    many = [_seat(tmp_path, name=f"PROJ/s{index}", tag=f"s{index}") for index in range(33)]
    with pytest.raises(GateConfigError, match="seats must list 1 to 32"):
        load_gate_config(write_config(tmp_path, seats=many))


@pytest.mark.parametrize(
    "content",
    ['{"schema": 1, "schema": 2}', "[]", "not json", b"\xff\xfe"],
)
def test_a_configuration_file_that_is_not_one_json_object_is_refused(
    tmp_path: Path, content: str | bytes
) -> None:
    with pytest.raises(GateConfigError):
        load_gate_config(owner_file(tmp_path / "gate.json", content))


def test_a_missing_configuration_is_refused(tmp_path: Path) -> None:
    with pytest.raises(GateConfigError, match="cannot read the gate configuration"):
        load_gate_config(tmp_path / "absent.json")


# --- the seats' grants -------------------------------------------------------------------


def test_a_messaging_seat_passes_the_grant_check(tmp_path: Path) -> None:
    grant_file(tmp_path / "grants.json", {"PROJ/bot": MESSAGING_TOOLS})
    check_seat_grants(load_gate_config(write_config(tmp_path)))


def test_a_seat_with_a_work_changing_grant_is_refused(tmp_path: Path) -> None:
    grant_file(tmp_path / "grants.json", {"PROJ/bot": [*MESSAGING_TOOLS, "synapse_claim"]})
    with pytest.raises(GateConfigError, match="may not hold: synapse_claim"):
        check_seat_grants(load_gate_config(write_config(tmp_path)))
    assert "synapse_send" not in GATE_DENIED_TOOLS


def test_a_seat_the_grant_file_does_not_grant_is_refused(tmp_path: Path) -> None:
    grant_file(tmp_path / "grants.json", {"PROJ/other": MESSAGING_TOOLS})
    with pytest.raises(GateConfigError, match="grants no subject the seat PROJ/bot"):
        check_seat_grants(load_gate_config(write_config(tmp_path)))


def test_an_unusable_grant_file_is_refused(tmp_path: Path) -> None:
    owner_file(tmp_path / "grants.json", "{}")
    with pytest.raises(GateConfigError, match="grant file cannot be used"):
        check_seat_grants(load_gate_config(write_config(tmp_path)))


# --- bearers -----------------------------------------------------------------------------


def test_a_new_bearer_is_stored_as_its_digest_only(tmp_path: Path) -> None:
    path = tmp_path / "bot.bearer"
    secret = new_bearer(path, now=NOW)
    assert len(secret) == 43
    stored = path.read_text(encoding="ascii")
    assert secret not in stored
    record = load_bearer(path)
    assert record.sha256 == bearer_digest(secret.encode("ascii"))
    assert record.issued_at == NOW
    assert record.expires_at == NOW + BEARER_LIFETIME
    assert path.stat().st_mode & 0o777 == 0o600


def test_a_bearer_is_never_overwritten_unless_it_is_rotated(tmp_path: Path) -> None:
    path = tmp_path / "bot.bearer"
    first = new_bearer(path)
    with pytest.raises(GateConfigError, match="use --replace"):
        new_bearer(path)
    second = new_bearer(path, replace=True)
    assert first != second
    assert load_bearer(path).sha256 == bearer_digest(second.encode("ascii"))
    assert sorted(item.name for item in tmp_path.iterdir()) == ["bot.bearer"]


def _bearer_record(**changes: object) -> str:
    record: dict[str, object] = {
        "schema": BEARER_SCHEMA,
        "sha256": "b" * 64,
        "issued_at": "2026-11-19T00:00:00Z",
        "expires_at": "2027-02-17T00:00:00Z",
    }
    record.update(changes)
    return json.dumps(record)


@pytest.mark.parametrize(
    "content",
    [
        "a-raw-secret-instead-of-a-digest",
        _bearer_record(schema="v0"),
        _bearer_record(extra=1),
        _bearer_record(sha256="B" * 64),
        _bearer_record(issued_at="2026-11-19T00:00:00"),
        _bearer_record(issued_at="yesterdayZ"),
        _bearer_record(expires_at=5),
        _bearer_record(expires_at="2026-11-18T00:00:00Z"),
    ],
)
def test_a_bearer_record_that_is_not_a_digest_record_is_refused(
    tmp_path: Path, content: str
) -> None:
    with pytest.raises(GateConfigError):
        load_bearer(owner_file(tmp_path / "bot.bearer", content))


def test_the_gate_refuses_to_start_on_a_raw_secret_bearer_file(tmp_path: Path) -> None:
    config = load_gate_config(write_config(tmp_path))
    owner_file(tmp_path / "bot.bearer", "raw-secret")
    with pytest.raises(GateConfigError, match="bearer record of PROJ/bot is unusable"):
        check_bearers(config)
    new_bearer(tmp_path / "bot.bearer", replace=True)
    check_bearers(config)


# --- seat tokens ---------------------------------------------------------------------------


def test_a_current_seat_token_is_read(tmp_path: Path) -> None:
    token = seat_token(int((NOW + timedelta(minutes=5)).timestamp()))
    path = owner_file(tmp_path / "bot.token", token + "\n")
    assert read_seat_token(path, now=NOW) == token.encode("ascii")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("not-a-token", "not an issuer token"),
        ("a.!!!.c", "not an issuer token"),
        ("a.e30x.c", "not an issuer token"),
        ("a." + base64.urlsafe_b64encode(b"[1]").decode().rstrip("=") + ".c", "no expiry"),
        (seat_token("soon"), "no expiry"),
        (seat_token(True), "no expiry"),
        (seat_token(int(NOW.timestamp())), "has expired"),
    ],
)
def test_an_unusable_seat_token_is_unavailable(tmp_path: Path, content: str, message: str) -> None:
    with pytest.raises(SeatTokenUnavailable, match=message):
        read_seat_token(owner_file(tmp_path / "bot.token", content), now=NOW)


def test_a_missing_seat_token_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(SeatTokenUnavailable, match="cannot be read"):
        read_seat_token(tmp_path / "absent.token")
