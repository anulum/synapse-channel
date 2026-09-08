# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — claim draft CLI integration tests
"""Exercise the real CLI and HTTP participant against a loopback protocol server.

The server supplies deterministic provider responses; no language model runs.
These tests prove transport, parsing and no-submission behaviour, not model quality.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.cli import main
from synapse_channel.cli_claim_parse import build_provider_invoke
from synapse_channel.participants.api_ollama import OllamaApiParticipant
from synapse_channel.participants.claim_parse import ProposalError, propose_claim
from synapse_channel.participants.headless_codex import CodexParticipant

ANSWER = '{"paths":["src/a.py"],"task":"repair"}'


@pytest.fixture
def provider_server() -> Iterator[tuple[str, dict[str, Any], list[dict[str, Any]]]]:
    """Serve a controllable generate response and record actual HTTP requests."""
    state: dict[str, Any] = {"response": ANSWER, "done": True}
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        """Handle only loopback test POSTs with bounded request reads."""

        def do_POST(self) -> None:
            """Record the wire body and emit the selected provider JSON."""
            size = int(self.headers["Content-Length"])
            assert 0 < size < 50000
            requests.append(json.loads(self.rfile.read(size)))
            body = json.dumps(state).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            """Keep deterministic test HTTP access logs off the terminal."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api/generate", state, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _arguments(endpoint: str) -> list[str]:
    """Build an explicitly opted-in request to the loopback-only provider."""
    return [
        "claim-parse",
        "--from-text=repair auth",
        "--provider=ollama-api",
        "--model=fixture",
        "--name=owner/seat",
        f"--endpoint={endpoint}",
        "--uri=ws://127.0.0.1:1",
        "--timeout=3",
    ]


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_round_trip_does_not_submit_to_hub(
    provider_server: tuple[str, dict[str, Any], list[dict[str, Any]]],
    capsys: pytest.CaptureFixture[str],
    as_json: bool,
) -> None:
    """Read a real HTTP answer despite an unreachable proposed hub and submit nothing."""
    endpoint, _state, requests = provider_server
    args = _arguments(endpoint) + (["--json"] if as_json else [])
    assert main(args) == 0
    output = capsys.readouterr().out
    if as_json:
        payload = json.loads(output)
        assert payload["submitted"] is False
        assert "--uri=ws://127.0.0.1:1" in payload["command"]
    else:
        assert "nothing has been submitted" in output
    assert len(requests) == 1
    assert requests[0]["model"] == "fixture"
    assert requests[0]["stream"] is False
    assert requests[0]["prompt"].endswith('"repair auth"')


def test_real_cli_subprocess_preserves_workspace(
    provider_server: tuple[str, dict[str, Any], list[dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """Run the actual module entry point with a real HTTP exchange and no workspace edits."""
    endpoint, _state, requests = provider_server
    canary = tmp_path / "owner-data"
    canary.write_bytes(b"preserve exactly")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-m", "synapse_channel.cli", *_arguments(endpoint), "--json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["submitted"] is False
    assert list(tmp_path.iterdir()) == [canary]
    assert canary.read_bytes() == b"preserve exactly"
    assert len(requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        "",
        "not JSON",
        '{"paths":["C:/Windows"],"task":"repair"}',
        '{"paths":["a"],"task":"bad\\u001b[31m"}',
    ],
)
def test_cli_refuses_provider_failures_and_invalid_drafts(
    provider_server: tuple[str, dict[str, Any], list[dict[str, Any]]],
    capsys: pytest.CaptureFixture[str],
    response: str,
) -> None:
    """Refuse malformed and failed responses after crossing the real HTTP boundary."""
    endpoint, state, requests = provider_server
    state["response"] = response
    assert main(_arguments(endpoint)) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "claim-parse:" in captured.err
    assert "\x1b" not in captured.err
    assert len(requests) == 1


@pytest.mark.parametrize(
    "extra",
    [
        ["--timeout=0"],
        ["--timeout=-1"],
        ["--timeout=nan"],
        ["--timeout=inf"],
        ["--timeout=invalid"],
        ["--name="],
        ["--name=bad\u2028name"],
        ["--uri=bad\u2029uri"],
        ["--name=bad\x1b"],
        ["--name=" + "a" * 201],
        ["--uri=bad\nuri"],
    ],
)
def test_cli_rejects_invalid_options_before_network(
    provider_server: tuple[str, dict[str, Any], list[dict[str, Any]]],
    extra: list[str],
) -> None:
    """Stop malformed operator values in argparse before contacting the provider."""
    endpoint, _state, requests = provider_server
    with pytest.raises(SystemExit) as exc:
        main(_arguments(endpoint) + extra)
    assert exc.value.code == 2
    assert requests == []


@pytest.mark.parametrize("missing", ["--provider=ollama-api", "--name=owner/seat"])
def test_cli_requires_provider_and_owner_opt_in(
    provider_server: tuple[str, dict[str, Any], list[dict[str, Any]]],
    missing: str,
) -> None:
    """Never infer provider cost consent or a proposed claim owner."""
    endpoint, _state, requests = provider_server
    with pytest.raises(SystemExit) as exc:
        main([arg for arg in _arguments(endpoint) if arg != missing])
    assert exc.value.code == 2
    assert not requests


def test_invalid_request_and_endpoint_options_make_no_network_request(
    provider_server: tuple[str, dict[str, Any], list[dict[str, Any]]],
) -> None:
    """Fail bad request data and mismatched endpoint configuration locally."""
    endpoint, _state, requests = provider_server
    assert main(_arguments(endpoint) + ["--from-text="]) == 1
    assert main(_arguments(endpoint) + ["--provider=codex"]) == 1
    assert main(_arguments(endpoint) + ["--model="]) == 1
    assert not requests


def test_provider_builder_uses_real_registered_driver(
    provider_server: tuple[str, dict[str, Any], list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise normal provider construction while binding its HTTP endpoint to loopback."""
    from synapse_channel.cli_participants import PROVIDERS

    endpoint, _state, requests = provider_server

    def builder(identity: str, *, model: str, timeout: float) -> OllamaApiParticipant:
        """Instantiate the production HTTP participant at the test server."""
        return OllamaApiParticipant(identity, model=model, timeout=timeout, endpoint=endpoint)

    monkeypatch.setitem(PROVIDERS, "ollama-api", builder)
    invoke = build_provider_invoke("ollama-api", identity="owner", model="fixture", timeout=3)
    assert propose_claim("repair auth", invoke=invoke).task == "repair"
    assert len(requests) == 1


def test_provider_refusals_do_not_start_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Respect the live schema refusal and required model contracts."""
    import synapse_channel.cli_participants as participants

    monkeypatch.setattr(participants, "GROK_SCHEMA_VERIFIED", False)
    for provider in ["grok", "unknown", "ollama-api"]:
        with pytest.raises(ProposalError):
            build_provider_invoke(provider, identity="owner", model="", timeout=3)


def test_abstained_real_process_turn_is_not_a_proposal(tmp_path: Path) -> None:
    """Drive a production headless participant through a real empty completed process."""
    emitter = tmp_path / "empty_turn.py"
    emitter.write_text('print(\'{"type":"turn.completed","usage":{}}\')\n')

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Replace only the external executable with a deterministic wire emitter."""
        return subprocess.run([sys.executable, str(emitter)], **kwargs)

    def builder(provider: str, **kwargs: Any) -> CodexParticipant:
        """Use the real driver, runner lifecycle, parser and turn envelope."""
        return CodexParticipant(**kwargs, runner=runner)

    invoke = build_provider_invoke(
        "codex", identity="owner", model="", timeout=3, participant_builder=builder
    )
    with pytest.raises(ProposalError, match="no proposal"):
        propose_claim("repair", invoke=invoke)
