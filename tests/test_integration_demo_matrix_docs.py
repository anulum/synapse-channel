"""Regression coverage for the public integration demo matrix."""

from __future__ import annotations

from pathlib import Path

from synapse_channel.demo_scenario import (
    DEMO_AGENT_LABELS,
    DEMO_PROVIDER_BOUNDARY,
    DEMO_STEP_TITLES,
)

ROOT = Path(__file__).resolve().parents[1]


def _read_doc(relative_path: str) -> str:
    """Return a public documentation file from the repository root."""
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_integration_demo_matrix_documents_the_golden_path_and_three_adapters() -> None:
    """The matrix should give users bounded demo choices, not vague promises."""
    matrix = _read_doc("docs/integration-demos.md")

    expected_sections = (
        "## Demo 0: Installed five-minute golden path",
        "## Demo 1: CLI coding sessions",
        "## Demo 2: MCP host adapter",
        "## Demo 3: Local A2A bridge",
    )
    for section in expected_sections:
        assert section in matrix

    expected_commands = (
        "synapse demo --output ./synapse-golden-demo",
        "synapse hub --host 127.0.0.1 --port 8876",
        "synapse git-init --name codex-1",
        "synapse git-claim --task-id DEMO-CLI --paths src --name codex-1",
        "pip install 'synapse-channel[mcp]'",
        "synapse mcp --uri ws://localhost:8876 --name claude-mcp",
        "synapse a2a-card --endpoint-url http://127.0.0.1:8877",
        "synapse a2a-serve --endpoint-url http://127.0.0.1:8877",
    )
    for command in expected_commands:
        assert command in matrix

    expected_boundaries = (
        "| Installed golden path | Supported, real local boundaries | Scripted "
        "in-process `SynapseAgent` identities labelled `CLAUDE`/`CODEX`, separate "
        "Git claims, deliberate conflict refusal, fail-closed mutation guard, atomic "
        "handoff, observed verification, receipt-backed release, static dashboard. "
        "| Uses a disposable local Git repository; it does not launch provider CLIs "
        "or model turns, and does not certify external client versions. |",
        "| CLI coding sessions | Supported | File-scope claims, claims release, "
        "board/status checks, direct messages. | Does not start or control the "
        "coding agent runtime. |",
        "| MCP host adapter | Supported adapter surface | MCP tools/resources expose "
        "Synapse coordination through stdio. | Does not certify every MCP host, "
        "streaming, tool chaining, or resource templates. |",
        "| Local A2A bridge | Local bridge surface | Agent Card projection plus "
        "local HTTP+JSON task/message routes. | Does not claim independent A2A "
        "conformance, remote TLS deployment, or real webhook receiver "
        "validation. |",
    )
    for row in expected_boundaries:
        assert row in matrix


def test_demo_docs_match_runtime_owned_labels_sequence_and_provider_boundary() -> None:
    """Public prose must describe the real scripted scenario, not vendor turns."""
    public_docs = (
        "README.md",
        "docs/quickstart.md",
        "docs/tutorial.md",
        "docs/integration-demos.md",
        "docs/cli.md",
        "examples/README.md",
    )
    combined = "\n".join(_read_doc(path) for path in public_docs)

    for label in DEMO_AGENT_LABELS:
        assert f"`{label}`" in combined
    for title in DEMO_STEP_TITLES.values():
        assert title in _read_doc("docs/integration-demos.md")
    assert "scripted in-process" in combined
    assert "no provider CLI" in combined
    assert "no model turn" in combined
    assert "scripted in-process SynapseAgent identities" in DEMO_PROVIDER_BOUNDARY

    marker = "demo-provider-boundary: scripted-in-process-no-provider-cli-no-model-turn"
    translations = sorted((ROOT / "docs" / "readme").glob("README.*.md"))
    assert len(translations) == 8
    for path in translations:
        text = path.read_text(encoding="utf-8")
        assert marker in text
        assert "`SynapseAgent`" in text
        assert "`CLAUDE`" in text
        assert "`CODEX`" in text


def test_integration_demo_matrix_is_cross_linked_from_public_docs() -> None:
    """README, CLI docs, MCP docs, and site nav should expose the matrix."""
    assert "[integration demo matrix](docs/integration-demos.md)" in _read_doc("README.md")
    assert "[Integration demos](integration-demos.md)" in _read_doc("docs/cli.md")
    assert "[integration demo matrix](integration-demos.md)" in _read_doc("docs/mcp.md")
    assert "Integration demos: integration-demos.md" in _read_doc("mkdocs.yml")


def test_integration_demo_matrix_keeps_conformance_claims_bounded() -> None:
    """The demo page must not close the external-validation TODO by wording."""
    matrix = _read_doc("docs/integration-demos.md").lower()

    forbidden_phrases = (
        "certified a2a",
        "full a2a conformance",
        "external mcp conformance",
        "production tls validated",
    )
    for phrase in forbidden_phrases:
        assert phrase not in matrix

    required_phrases = (
        "local-only",
        "external validation remains open",
        "real webhook receiver validation remains open",
    )
    for phrase in required_phrases:
        assert phrase in matrix
