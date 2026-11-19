# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — tests for the operator's local endpoint list
"""A local worker reaches only the model endpoints its operator declares.

These tests pin the rules T120-1 to T120-6 of the C120 unit contract: every HTTP
tier is checked when the worker is built, loopback is not assumed to be local,
matching is exact after normalisation, the default setup keeps working, and the
list is read strictly.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Coroutine, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli, cli_processes
from synapse_channel.client.chat_backends import OpenAIChatClient
from synapse_channel.client.llm_worker import (
    DEFAULT_OLLAMA_BASE_URL,
    OPENAI_DEFAULT_BASE_URL,
    SynapseLLMWorker,
)
from synapse_channel.client.local_endpoints import (
    DEFAULT_LOCAL_ENDPOINTS,
    LOCAL_ENDPOINTS_SCHEMA,
    LocalEndpointError,
    default_local_endpoints_path,
    endpoint_is_listed,
    load_local_endpoints,
    normalise_endpoint,
)
from synapse_channel.client.routing import TieredChatClient

HOSTED = "https://api.deepseek.com/v1"


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the default list path into the test's own directory."""
    config = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    yield config


def _list_file(path: Path, document: object) -> Path:
    """Write ``document`` as an owner-only list file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = document if isinstance(document, str) else json.dumps(document)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _worker(
    *,
    provider: str = "ollama",
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    model: str = "llama3",
    heavy_model: str = "",
    local_endpoints: Sequence[str] | None = None,
) -> SynapseLLMWorker:
    return SynapseLLMWorker(
        name="ALPHA",
        provider=provider,
        base_url=base_url,
        model=model,
        heavy_model=heavy_model,
        local_endpoints=local_endpoints,
    )


def _http_base(worker: SynapseLLMWorker) -> str:
    assert isinstance(worker.client, OpenAIChatClient)
    return worker.client.base_url


def _close(coroutine: Coroutine[Any, Any, None]) -> None:
    coroutine.close()


# --- T120-1 and T120-2: every HTTP tier is checked at construction ------------------------


def test_a_tiered_worker_on_a_hosted_endpoint_is_refused_for_each_tier() -> None:
    with pytest.raises(LocalEndpointError, match=r"the SLM endpoint .*deepseek.* not a declared"):
        _worker(provider="tiered", model="small", heavy_model="big", base_url=HOSTED)
    rule = _worker(provider="rule", base_url=HOSTED, local_endpoints=[])
    for tier in ("SLM", "HEAVY"):
        with pytest.raises(LocalEndpointError, match=f"the {tier} endpoint"):
            rule._http_client("m", tier=tier)


def test_an_ollama_worker_on_a_hosted_endpoint_is_refused() -> None:
    with pytest.raises(LocalEndpointError, match="the ollama endpoint"):
        _worker(provider="ollama", base_url=HOSTED)
    # Only the exact OpenAI default is redirected to Ollama; another spelling is refused.
    with pytest.raises(LocalEndpointError, match="the ollama endpoint"):
        _worker(provider="ollama", base_url=f"{OPENAI_DEFAULT_BASE_URL}/")


def test_the_refusal_is_a_configuration_error_of_the_worker_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = cli.build_parser().parse_args(["worker", "--provider", "ollama", "--base-url", HOSTED])
    assert cli_processes._cmd_worker(args, runner=_close) == 2
    assert "configuration error: the ollama endpoint" in capsys.readouterr().err


# --- T120-3: loopback is not local unless listed ------------------------------------------


def test_a_loopback_gateway_is_refused_unless_it_is_listed() -> None:
    gateway = "http://localhost:4000/v1"
    with pytest.raises(LocalEndpointError, match="the ollama endpoint"):
        _worker(base_url=gateway)
    assert _http_base(_worker(base_url=gateway, local_endpoints=[gateway])) == gateway


# --- T120-4: exact matching after normalisation -------------------------------------------


def test_matching_normalises_case_ports_and_the_trailing_slash() -> None:
    listed = ["http://ml350-host/v1"]
    assert _http_base(_worker(base_url="HTTP://ML350-HOST:80/v1/", local_endpoints=listed))
    assert endpoint_is_listed("http://ml350-host/v1/chat", listed)
    assert not endpoint_is_listed("http://ml350-host/v1x", listed)
    assert not endpoint_is_listed("https://ml350-host/v1", listed)
    assert not endpoint_is_listed("http://ml350-host:8080/v1", listed)
    assert not endpoint_is_listed("http://ml350-host.example/v1", listed)
    assert endpoint_is_listed("https://gpu.example/anything", ["https://gpu.example:443"])
    assert normalise_endpoint("HTTPS://GPU.Example/v1/") == ("https", "gpu.example", 443, "/v1")


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("gpu:11434/v1", "http or https URL with a host"),
        ("ftp://gpu/v1", "http or https URL with a host"),
        ("http:///v1", "http or https URL with a host"),
        ("http://user:secret@gpu/v1", "must not carry credentials"),
        ("http://gpu/v1?key=1", "query or a fragment"),
        ("http://gpu/v1#part", "query or a fragment"),
        ("http://gpu:99999/v1", "invalid port"),
    ],
)
def test_an_endpoint_that_is_not_a_plain_http_base_is_refused(url: str, reason: str) -> None:
    with pytest.raises(LocalEndpointError, match=reason) as caught:
        normalise_endpoint(url)
    assert "secret" not in str(caught.value)


# --- T120-5: the default setup keeps working ----------------------------------------------


def test_without_a_list_file_the_local_ollama_default_still_works(isolated_config: Path) -> None:
    assert not default_local_endpoints_path().exists()
    assert load_local_endpoints() == DEFAULT_LOCAL_ENDPOINTS
    assert _http_base(_worker()) == DEFAULT_OLLAMA_BASE_URL
    assert _http_base(_worker(base_url=OPENAI_DEFAULT_BASE_URL)) == DEFAULT_OLLAMA_BASE_URL
    assert isinstance(
        _worker(provider="tiered", model="small", heavy_model="big").client, TieredChatClient
    )
    loopback = "http://127.0.0.1:11434/v1"
    assert _http_base(_worker(base_url=loopback)) == loopback


def test_a_rule_or_paid_worker_never_reads_the_list(isolated_config: Path) -> None:
    _list_file(default_local_endpoints_path(), "not json")
    assert _worker(provider="rule").client is not None


def test_the_default_list_file_is_read_when_present(isolated_config: Path) -> None:
    listed = "http://gpu.lan:11434/v1"
    _list_file(
        default_local_endpoints_path(), {"schema": LOCAL_ENDPOINTS_SCHEMA, "endpoints": [listed]}
    )
    assert load_local_endpoints() == (listed,)
    assert _http_base(_worker(base_url=listed)) == listed
    with pytest.raises(LocalEndpointError, match="the ollama endpoint"):
        _worker()  # the list replaces the default, so localhost is no longer declared


def test_the_default_path_follows_xdg_config_home_or_the_home_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert default_local_endpoints_path({"XDG_CONFIG_HOME": "/x"}) == Path(
        "/x/synapse/local-endpoints.json"
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_local_endpoints_path({}) == tmp_path / ".config/synapse/local-endpoints.json"


def test_the_worker_command_reads_an_explicit_list(tmp_path: Path) -> None:
    gateway = "http://localhost:4000/v1"
    listed = _list_file(
        tmp_path / "endpoints.json", {"schema": LOCAL_ENDPOINTS_SCHEMA, "endpoints": [gateway]}
    )
    built: list[SynapseLLMWorker] = []
    args = cli.build_parser().parse_args(
        ["worker", "--base-url", gateway, "--local-endpoints", str(listed)]
    )

    assert cli_processes._cmd_worker(args, runner=_close, on_worker=built.append) == 0
    assert _http_base(built[0]) == gateway


# --- T120-6: the list is read strictly ----------------------------------------------------


@pytest.mark.parametrize(
    "document",
    [
        "not json",
        "[]",
        {"schema": LOCAL_ENDPOINTS_SCHEMA},
        {"schema": "synapse-local-endpoints.v0", "endpoints": []},
        {"schema": LOCAL_ENDPOINTS_SCHEMA, "endpoints": "http://gpu/v1"},
        {"schema": LOCAL_ENDPOINTS_SCHEMA, "endpoints": [1]},
        {"schema": LOCAL_ENDPOINTS_SCHEMA, "endpoints": [], "extra": True},
        {"schema": LOCAL_ENDPOINTS_SCHEMA, "endpoints": ["gpu:11434/v1"]},
    ],
)
def test_a_list_not_in_the_documented_shape_is_refused(tmp_path: Path, document: object) -> None:
    with pytest.raises(LocalEndpointError):
        load_local_endpoints(_list_file(tmp_path / "endpoints.json", document))


def test_an_empty_list_declares_nothing_local(tmp_path: Path) -> None:
    empty = _list_file(
        tmp_path / "endpoints.json", {"schema": LOCAL_ENDPOINTS_SCHEMA, "endpoints": []}
    )
    assert load_local_endpoints(empty) == ()
    with pytest.raises(LocalEndpointError, match="the ollama endpoint"):
        _worker(local_endpoints=load_local_endpoints(empty))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits and symlinks")
def test_a_symlinked_or_shared_or_missing_list_is_refused(tmp_path: Path) -> None:
    real = _list_file(
        tmp_path / "real.json", {"schema": LOCAL_ENDPOINTS_SCHEMA, "endpoints": [HOSTED]}
    )
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(LocalEndpointError, match="cannot read the local endpoint list"):
        load_local_endpoints(link)
    real.chmod(0o644)
    with pytest.raises(LocalEndpointError, match="cannot read the local endpoint list"):
        load_local_endpoints(real)
    with pytest.raises(LocalEndpointError, match="cannot read the local endpoint list"):
        load_local_endpoints(tmp_path / "absent.json")
