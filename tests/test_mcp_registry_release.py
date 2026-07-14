# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — MCP public-distribution release verifier tests

from __future__ import annotations

import importlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import ModuleType

import pytest
from tools.mcp_registry_release import (
    OWNERSHIP_MARKER,
    REGISTRY_NAME,
    ReleaseContractError,
    VerificationUnavailable,
    fetch_json,
    load_contract,
    verify_distribution,
)
from tools.verify_mcp_registry_release import main

_VERSION = "0.99.7"


class _FixtureServer(ThreadingHTTPServer):
    """Threaded HTTP fixture carrying exact path-to-response mappings."""

    routes: Mapping[str, tuple[int, bytes]]

    def __init__(self, routes: Mapping[str, tuple[int, bytes]]) -> None:
        super().__init__(("127.0.0.1", 0), _JsonHandler)
        self.routes = routes


class _JsonHandler(BaseHTTPRequestHandler):
    """Serve recorded PyPI and MCP Registry response shapes over real HTTP."""

    def do_GET(self) -> None:
        server = self.server
        assert isinstance(server, _FixtureServer)
        status, payload = server.routes.get(self.path, (404, b'{"error":"not found"}'))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        """Keep fixture traffic out of focused-test output."""


@contextmanager
def _serve(routes: Mapping[str, tuple[int, bytes]]) -> Iterator[str]:
    server = _FixtureServer(routes)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        address = server.server_address
        assert isinstance(address, tuple)
        host, port = address[0], address[1]
        assert isinstance(host, str)
        assert isinstance(port, int)
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _json_payload(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


def _write_contract(root: Path, *, version: str = _VERSION) -> None:
    (root / "server.json").write_text(
        json.dumps(
            {
                "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
                "name": REGISTRY_NAME,
                "version": version,
                "packages": [
                    {
                        "registryType": "pypi",
                        "identifier": "synapse-channel",
                        "version": version,
                        "transport": {"type": "stdio"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "synapse-channel"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "README.md").write_text(f"<!-- {OWNERSHIP_MARKER} -->\n", encoding="utf-8")


def _pypi(
    *,
    version: str = _VERSION,
    marker: bool = True,
    files: tuple[str, ...] = ("bdist_wheel", "sdist"),
) -> object:
    description = f"<!-- {OWNERSHIP_MARKER} -->" if marker else "marker missing"
    return {
        "info": {"version": version, "description": description},
        "urls": [{"packagetype": kind} for kind in files],
    }


def _registry(
    *,
    version: str = _VERSION,
    status: str = "active",
    latest: bool = True,
    package: str = "synapse-channel",
    published: str | None = "2026-07-14T10:00:00Z",
) -> object:
    official: dict[str, object] = {"status": status, "isLatest": latest}
    if published is not None:
        official["publishedAt"] = published
    return {
        "servers": [
            {
                "server": {
                    "name": REGISTRY_NAME,
                    "version": version,
                    "packages": [
                        {
                            "registryType": "pypi",
                            "identifier": package,
                            "version": version,
                            "transport": {"type": "stdio"},
                        }
                    ],
                },
                "_meta": {"io.modelcontextprotocol.registry/official": official},
            }
        ]
    }


def _routes(
    *, pypi: object | None = None, registry: object | None = None
) -> dict[str, tuple[int, bytes]]:
    routes = {
        f"/pypi/synapse-channel/{_VERSION}/json": (
            200,
            _json_payload(_pypi() if pypi is None else pypi),
        )
    }
    if registry is not None:
        routes["/v0.1/servers?search=io.github.anulum%2Fsynapse-channel"] = (
            200,
            _json_payload(registry),
        )
    return routes


def test_load_contract_accepts_the_exact_local_distribution_identity(tmp_path: Path) -> None:
    _write_contract(tmp_path)

    contract = load_contract(tmp_path, expect_version=_VERSION)

    assert contract.name == REGISTRY_NAME
    assert contract.package == "synapse-channel"
    assert contract.version == _VERSION


def test_load_contract_uses_the_supported_python_310_tomli_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_contract(tmp_path)
    real_import = importlib.import_module

    def import_without_tomllib(module_name: str) -> ModuleType:
        if module_name == "tomllib":
            raise ModuleNotFoundError("tomllib unavailable", name=module_name)
        return real_import(module_name)

    monkeypatch.setattr(
        "tools.mcp_registry_release.importlib.import_module", import_without_tomllib
    )

    assert load_contract(tmp_path).version == _VERSION


def test_load_contract_fails_closed_without_a_toml_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_contract(tmp_path)

    def import_without_parser(module_name: str) -> ModuleType:
        raise ModuleNotFoundError(f"{module_name} unavailable", name=module_name)

    monkeypatch.setattr("tools.mcp_registry_release.importlib.import_module", import_without_parser)

    with pytest.raises(ReleaseContractError, match="TOML metadata needs"):
        load_contract(tmp_path)


@pytest.mark.parametrize("drift", ("schema", "name", "version", "marker", "package"))
def test_load_contract_rejects_every_local_identity_drift(tmp_path: Path, drift: str) -> None:
    _write_contract(tmp_path)
    if drift == "marker":
        (tmp_path / "README.md").write_text("missing\n", encoding="utf-8")
    elif drift == "version":
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "synapse-channel"\nversion = "9.9.9"\n',
            encoding="utf-8",
        )
    else:
        server = json.loads((tmp_path / "server.json").read_text(encoding="utf-8"))
        if drift == "schema":
            server["$schema"] = "https://invalid.example/schema.json"
        elif drift == "name":
            server["name"] = "invalid/name"
        else:
            server["packages"][0]["identifier"] = "other"
        (tmp_path / "server.json").write_text(json.dumps(server), encoding="utf-8")

    with pytest.raises(ReleaseContractError):
        load_contract(tmp_path, expect_version=_VERSION)


def test_load_contract_reports_unreadable_shapes_and_missing_package(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    (tmp_path / "server.json").write_text("{", encoding="utf-8")
    with pytest.raises(ReleaseContractError, match="cannot read"):
        load_contract(tmp_path)

    (tmp_path / "server.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ReleaseContractError, match="JSON object"):
        load_contract(tmp_path)

    _write_contract(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project", encoding="utf-8")
    with pytest.raises(ReleaseContractError, match="package metadata"):
        load_contract(tmp_path)

    _write_contract(tmp_path)
    (tmp_path / "pyproject.toml").write_text('project = "invalid"\n', encoding="utf-8")
    with pytest.raises(ReleaseContractError, match="project metadata must be a table"):
        load_contract(tmp_path)

    _write_contract(tmp_path)
    server = json.loads((tmp_path / "server.json").read_text(encoding="utf-8"))
    server["packages"] = []
    (tmp_path / "server.json").write_text(json.dumps(server), encoding="utf-8")
    with pytest.raises(ReleaseContractError, match="exactly one package"):
        load_contract(tmp_path)


def test_package_phase_verifies_exact_public_pypi_artefacts_over_http(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    contract = load_contract(tmp_path)
    with _serve(_routes()) as base:
        result = verify_distribution(
            contract,
            phase="package",
            pypi_api_base=f"{base}/pypi",
            registry_api=f"{base}/v0.1/servers",
        )

    assert result.ok is True
    assert result.pypi_file_types == ("bdist_wheel", "sdist")
    assert result.registry_status is None


def test_registry_phase_verifies_active_latest_exact_version_over_http(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    contract = load_contract(tmp_path)
    with _serve(_routes(registry=_registry())) as base:
        result = verify_distribution(
            contract,
            phase="registry",
            pypi_api_base=f"{base}/pypi",
            registry_api=f"{base}/v0.1/servers",
        )

    assert result.ok is True
    assert result.registry_status == "active"
    assert result.registry_is_latest is True
    assert result.registry_published_at == "2026-07-14T10:00:00Z"
    assert result.as_json()["ok"] is True


def test_verifier_reports_package_and_registry_contract_mismatches(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    contract = load_contract(tmp_path)
    bad_pypi = _pypi(version="0.99.6", marker=False, files=("bdist_wheel",))
    bad_registry = _registry(status="deprecated", latest=False, package="other", published=None)
    with _serve(_routes(pypi=bad_pypi, registry=bad_registry)) as base:
        result = verify_distribution(
            contract,
            phase="registry",
            pypi_api_base=f"{base}/pypi",
            registry_api=f"{base}/v0.1/servers",
        )

    joined = "\n".join(result.errors)
    assert result.ok is False
    assert "PyPI package version" in joined
    assert "ownership marker" in joined
    assert "sdist" in joined
    assert "registry package identity" in joined
    assert "not active" in joined
    assert "not marked latest" in joined
    assert "publication timestamp" in joined


def test_pypi_response_without_info_or_files_fails_closed(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    contract = load_contract(tmp_path)
    with _serve(_routes(pypi={})) as base:
        result = verify_distribution(
            contract,
            phase="package",
            pypi_api_base=f"{base}/pypi",
            registry_api=f"{base}/v0.1/servers",
        )

    joined = "\n".join(result.errors)
    assert "no package info" in joined
    assert "ownership marker" in joined
    assert "bdist_wheel, sdist" in joined


def test_registry_phase_refuses_to_infer_publication_from_an_older_record(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    contract = load_contract(tmp_path)
    with _serve(_routes(registry=_registry(version="0.99.2"))) as base:
        result = verify_distribution(
            contract,
            phase="registry",
            pypi_api_base=f"{base}/pypi",
            registry_api=f"{base}/v0.1/servers",
        )

    assert result.errors == ("official registry has no exact-version server record",)


def test_registry_response_without_a_server_list_fails_closed(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    contract = load_contract(tmp_path)
    with _serve(_routes(registry={"metadata": {"count": 0}})) as base:
        result = verify_distribution(
            contract,
            phase="registry",
            pypi_api_base=f"{base}/pypi",
            registry_api=f"{base}/v0.1/servers",
        )

    assert result.errors == ("official registry has no exact-version server record",)


@pytest.mark.parametrize(
    ("status", "payload"),
    ((503, b'{"error":"down"}'), (200, b"not-json"), (200, b"[]")),
)
def test_fetch_json_reports_public_evidence_as_unavailable(status: int, payload: bytes) -> None:
    with _serve({"/evidence": (status, payload)}) as base:
        with pytest.raises(VerificationUnavailable):
            fetch_json(f"{base}/evidence", 2.0)


def test_fetch_json_refuses_non_http_urls_and_connection_failures() -> None:
    with pytest.raises(VerificationUnavailable, match="refusing non-HTTP"):
        fetch_json("file:///etc/passwd", 2.0)
    with pytest.raises(VerificationUnavailable, match="invalid HTTP evidence URL"):
        fetch_json("http://127.0.0.1:not-a-port/evidence", 2.0)
    with pytest.raises(VerificationUnavailable, match="cannot fetch"):
        fetch_json("http://127.0.0.1:1/evidence", 0.1)


def test_cli_exit_codes_separate_match_drift_and_unavailable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_contract(tmp_path)
    with _serve(_routes(registry=_registry())) as base:
        assert (
            main(
                ["--phase", "registry", "--expect-version", _VERSION, "--json"],
                root=tmp_path,
                pypi_api_base=f"{base}/pypi",
                registry_api=f"{base}/v0.1/servers",
            )
            == 0
        )
        evidence = json.loads(capsys.readouterr().out)
        assert evidence["ok"] is True

    with _serve(_routes(registry={"servers": []})) as base:
        assert (
            main(
                ["--phase", "registry"],
                root=tmp_path,
                pypi_api_base=f"{base}/pypi",
                registry_api=f"{base}/v0.1/servers",
            )
            == 1
        )
        assert "no exact-version" in capsys.readouterr().err

    assert main(["--expect-version", "9.9.9"], root=tmp_path) == 2
    assert "expected '9.9.9'" in capsys.readouterr().err

    with _serve({f"/pypi/synapse-channel/{_VERSION}/json": (503, b"{}")}) as base:
        assert main(["--phase", "package"], root=tmp_path, pypi_api_base=f"{base}/pypi") == 2
        assert "unavailable" in capsys.readouterr().err


def test_cli_rejects_bad_timeout_and_prints_plain_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_contract(tmp_path)
    with pytest.raises(SystemExit):
        main(["--timeout", "0"], root=tmp_path)
    capsys.readouterr()

    with _serve(_routes()) as base:
        assert (
            main(
                ["--phase", "package"],
                root=tmp_path,
                pypi_api_base=f"{base}/pypi",
            )
            == 0
        )
    assert "MCP package verification passed" in capsys.readouterr().out
