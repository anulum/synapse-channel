# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the wait --capability-card registration hook

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel import cli, cli_messaging
from synapse_channel.cli_messaging_wait import _load_dispatch_card
from synapse_channel.core.hub import SynapseHub

# --- card loader ---------------------------------------------------------------


def test_load_dispatch_card_full_document(tmp_path: Path) -> None:
    path = tmp_path / "card.json"
    path.write_text(
        json.dumps(
            {
                "description": "audit seat",
                "skills": ["mypy", "ruff"],
                "task_classes": ["audit", "review"],
                "model": "kimi",
                "dispatchable": False,
            }
        ),
        encoding="utf-8",
    )
    assert _load_dispatch_card(path) == {
        "description": "audit seat",
        "skills": ["mypy", "ruff"],
        "task_classes": ["audit", "review"],
        "model": "kimi",
        "dispatchable": False,
    }


def test_load_dispatch_card_minimal_document(tmp_path: Path) -> None:
    path = tmp_path / "card.json"
    path.write_text("{}", encoding="utf-8")
    assert _load_dispatch_card(path) == {}


def test_load_dispatch_card_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _load_dispatch_card(tmp_path / "absent.json") is None
    assert "continuing unregistered" in capsys.readouterr().out


def test_load_dispatch_card_invalid_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "card.json"
    path.write_text("{not json", encoding="utf-8")
    assert _load_dispatch_card(path) is None
    assert "invalid JSON" in capsys.readouterr().out


def test_load_dispatch_card_non_object_top_level(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "card.json"
    path.write_text('["audit"]', encoding="utf-8")
    assert _load_dispatch_card(path) is None
    assert "must be a JSON object" in capsys.readouterr().out


@pytest.mark.parametrize("key,value", [("description", 3), ("model", ["x"])])
def test_load_dispatch_card_scalar_type_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], key: str, value: object
) -> None:
    path = tmp_path / "card.json"
    path.write_text(json.dumps({key: value}), encoding="utf-8")
    assert _load_dispatch_card(path) is None
    assert key in capsys.readouterr().out


@pytest.mark.parametrize("key", ["skills", "task_classes"])
def test_load_dispatch_card_list_type_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], key: str
) -> None:
    path = tmp_path / "card.json"
    path.write_text(json.dumps({key: "audit"}), encoding="utf-8")
    assert _load_dispatch_card(path) is None
    assert key in capsys.readouterr().out
    path.write_text(json.dumps({key: ["audit", 3]}), encoding="utf-8")
    assert _load_dispatch_card(path) is None


def test_load_dispatch_card_dispatchable_type_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "card.json"
    path.write_text(json.dumps({"dispatchable": "no"}), encoding="utf-8")
    assert _load_dispatch_card(path) is None
    assert "boolean" in capsys.readouterr().out


def test_parser_wait_capability_card() -> None:
    args = cli.build_parser().parse_args(["wait", "--capability-card", "card.json"])
    assert args.capability_card == "card.json"


# --- live hub hook --------------------------------------------------------------


async def _wait_for_presence(observer: AgentHandle, name: str) -> None:
    await observer.recorder.wait_for(
        lambda message: message.get("type") == "presence_update" and message.get("agent") == name
    )


async def test_wait_registers_persistent_card_for_seat(tmp_path: Path) -> None:
    card_path = tmp_path / "card.json"
    card_path.write_text(
        json.dumps({"description": "audit seat", "task_classes": ["audit"]}),
        encoding="utf-8",
    )
    async with running_hub(SynapseHub()) as (hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="PROJ/kimi-3dcd-rx",
                for_name="PROJ/kimi-3dcd",
                timeout=2.0,
                capability_card_path=card_path,
            )
        )
        try:
            await _wait_for_presence(observer, "PROJ/kimi-3dcd-rx")
            for _ in range(100):
                if hub.capabilities.get_persistent("PROJ/kimi-3dcd") is not None:
                    break
                await asyncio.sleep(0.02)
            entry = hub.capabilities.get_persistent("PROJ/kimi-3dcd")
            assert entry is not None
            assert entry.card.description == "audit seat"
            assert entry.card.task_classes == ("audit",)
            sender = await connect_agent("A", uri)
            try:
                await sender.agent.chat("wake up", target="PROJ/kimi-3dcd")
            finally:
                await close_agents(sender)
            code = await wait_task
        finally:
            await close_agents(observer)
    assert code == 0


async def test_wait_with_malformed_card_stays_up_unregistered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    card_path = tmp_path / "card.json"
    card_path.write_text("{not json", encoding="utf-8")
    async with running_hub(SynapseHub()) as (hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="PROJ/kimi-3dcd-rx",
                for_name="PROJ/kimi-3dcd",
                timeout=0.5,
                capability_card_path=card_path,
            )
        )
        try:
            await _wait_for_presence(observer, "PROJ/kimi-3dcd-rx")
            code = await wait_task
        finally:
            await close_agents(observer)
    assert code == 2
    assert hub.capabilities.get_persistent("PROJ/kimi-3dcd") is None
    assert "continuing unregistered" in capsys.readouterr().out


async def test_wait_with_failing_advertise_stays_up(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    card_path = tmp_path / "card.json"
    card_path.write_text(json.dumps({"description": "seat"}), encoding="utf-8")

    class _FailingAdvertiseAgent:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.running = True
            self.last_close_code: int | None = None
            self.last_close_reason = ""

        async def connect(self) -> None:
            await asyncio.Event().wait()

        async def wait_until_ready(self, timeout: float) -> bool:
            del timeout
            return True

        async def advertise(self, **_kwargs: object) -> None:
            raise RuntimeError("simulated send failure")

    code = await cli_messaging._wait(
        uri="ws://unused",
        name="PROJ/seat-1-rx",
        for_name="PROJ/seat-1",
        timeout=0.2,
        agent_factory=_FailingAdvertiseAgent,  # type: ignore[arg-type]
        capability_card_path=card_path,
    )
    assert code == 2
    assert "advertise failed" in capsys.readouterr().out


async def test_wait_periodically_refreshes_the_registration(tmp_path: Path) -> None:
    card_path = tmp_path / "card.json"
    card_path.write_text(json.dumps({"description": "seat"}), encoding="utf-8")
    advertise_calls: list[dict[str, object]] = []

    class _RecordingAgent:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.running = True
            self.last_close_code: int | None = None
            self.last_close_reason = ""

        async def connect(self) -> None:
            await asyncio.Event().wait()

        async def wait_until_ready(self, timeout: float) -> bool:
            del timeout
            return True

        async def advertise(self, **kwargs: object) -> None:
            advertise_calls.append(kwargs)

    code = await cli_messaging._wait(
        uri="ws://unused",
        name="PROJ/seat-1-rx",
        for_name="PROJ/seat-1",
        timeout=0.35,
        agent_factory=_RecordingAgent,  # type: ignore[arg-type]
        capability_card_path=card_path,
        capability_refresh_seconds=0.1,
    )
    assert code == 2
    assert len(advertise_calls) >= 2
    assert all(call["persist"] is True for call in advertise_calls)
    assert all(call["agent"] == "PROJ/seat-1" for call in advertise_calls)


async def test_wait_without_refresh_interval_advertises_once(tmp_path: Path) -> None:
    card_path = tmp_path / "card.json"
    card_path.write_text(json.dumps({"description": "seat"}), encoding="utf-8")
    advertise_calls: list[dict[str, object]] = []

    class _RecordingAgent:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.running = True
            self.last_close_code: int | None = None
            self.last_close_reason = ""

        async def connect(self) -> None:
            await asyncio.Event().wait()

        async def wait_until_ready(self, timeout: float) -> bool:
            del timeout
            return True

        async def advertise(self, **kwargs: object) -> None:
            advertise_calls.append(kwargs)

    code = await cli_messaging._wait(
        uri="ws://unused",
        name="PROJ/seat-1-rx",
        for_name="PROJ/seat-1",
        timeout=0.2,
        agent_factory=_RecordingAgent,  # type: ignore[arg-type]
        capability_card_path=card_path,
        capability_refresh_seconds=0.0,
    )
    assert code == 2
    assert len(advertise_calls) == 1


def test_parser_wait_capability_refresh_seconds() -> None:
    args = cli.build_parser().parse_args(
        ["wait", "--capability-card", "card.json", "--capability-refresh-seconds", "60"]
    )
    assert args.capability_refresh_seconds == 60.0
    default = cli.build_parser().parse_args(["wait"])
    assert default.capability_refresh_seconds == 21_600.0
