# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — measure the byte/token cost of the lite relay encoding
"""Measure how much the lite relay encoding shrinks a channel feed.

A token-budgeted agent can watch the channel either by reading the hub's live
JSON frames or by tailing the compact lite relay log
(:func:`synapse_channel.relay.encode_lite`). This benchmark replays a fixed,
committed trace of broadcast envelopes and reports, for each and in total, the
size of three serialisations so the saving is decomposed honestly:

* ``raw_wire`` — the full envelope with default ``json.dumps`` spacing, exactly
  what the hub broadcasts on the socket;
* ``raw_compact`` — the same full envelope minified, isolating whitespace savings;
* ``lite`` — :func:`~synapse_channel.relay.encode_lite` output, minified, as
  written to the relay log.

The version-2 lite format preserves every JSON payload and auxiliary envelope
field; only timestamp precision is deliberately reduced to milliseconds.
``roundtrip_envelope_fidelity`` compares the decoded row with an independent
normalisation of the full input envelope, so dropped grant fields cannot hide
behind a core-field-only oracle.

Byte counts are exact and dependency-free. Token counts use ``tiktoken``'s
``cl100k_base`` when installed (``pip install -e ".[benchmark]"``); without it
they fall back to a deterministic, clearly-labelled characters-per-token
estimate so the script still runs, and the method is recorded in the output.

Run with ``python benchmarks/relay_token_benchmark.py``; results are written to
``benchmarks/results/relay_token_benchmark.json``.
"""

from __future__ import annotations

import argparse
import importlib
import json
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from synapse_channel.relay import decode_lite, encode_lite

ENCODING_NAME = "cl100k_base"
HEURISTIC_NAME = "chars-per-token-4 (no tiktoken installed)"
HEURISTIC_DIVISOR = 4

BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_TRACE = BENCHMARK_DIR / "traces" / "sample_session.json"
DEFAULT_RESULTS = BENCHMARK_DIR / "results" / "relay_token_benchmark.json"


class Encoder(Protocol):
    """Minimal tokeniser surface this benchmark relies on."""

    def encode(self, text: str) -> list[int]:
        """Return the token ids for ``text``."""


def get_encoder(
    import_module: Callable[[str], Any] = importlib.import_module,
) -> tuple[Encoder | None, str]:
    """Return a ``cl100k_base`` encoder and its name, or a heuristic fallback.

    Returns
    -------
    tuple[Encoder or None, str]
        The tokeniser (``None`` when ``tiktoken`` is unavailable) and a label
        naming the token-counting method actually used.
    """
    try:
        tiktoken = import_module("tiktoken")
        encoder: Encoder = tiktoken.get_encoding(ENCODING_NAME)
        return encoder, ENCODING_NAME
    except Exception:
        return None, HEURISTIC_NAME


def count_tokens(text: str, encoder: Encoder | None) -> int:
    """Count tokens in ``text`` with ``encoder``, or a deterministic estimate.

    Parameters
    ----------
    text : str
        The serialised message to measure.
    encoder : Encoder or None
        A tokeniser, or ``None`` to use the characters-per-token estimate.

    Returns
    -------
    int
        Token count (real when an encoder is given, otherwise an estimate of at
        least ``1`` for any non-empty text).
    """
    if encoder is not None:
        return len(encoder.encode(text))
    if not text:
        return 0
    return max(1, round(len(text) / HEURISTIC_DIVISOR))


def _compact(obj: dict[str, Any]) -> str:
    """Serialise ``obj`` as minified JSON, the form written to the relay log."""
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":"))


def _expected_roundtrip(envelope: dict[str, Any]) -> dict[str, Any]:
    """Return the full valid envelope after documented codec normalisation."""
    expected = dict(envelope)
    expected["sender"] = str(envelope.get("sender", "?"))
    expected["target"] = str(envelope.get("target", "all"))
    expected["type"] = str(envelope.get("type", "chat"))
    expected["payload"] = envelope.get("payload", "")
    expected["timestamp"] = int(float(envelope["timestamp"]) * 1000.0) / 1000.0
    expected["msg_id"] = int(envelope.get("msg_id", 0))
    expected["hub_id"] = str(envelope.get("hub_id", ""))
    channel = str(envelope.get("channel") or "").strip()
    if channel:
        expected["channel"] = channel
    else:
        expected.pop("channel", None)
    return expected


def measure_message(envelope: dict[str, Any], encoder: Encoder | None) -> dict[str, Any]:
    """Measure one envelope across the three serialisations.

    Parameters
    ----------
    envelope : dict[str, Any]
        A full broadcast envelope from the trace.
    encoder : Encoder or None
        Tokeniser passed to :func:`count_tokens`.

    Returns
    -------
    dict[str, Any]
        Per-message byte and token counts for ``raw_wire``, ``raw_compact``,
        and ``lite``, plus ``roundtrip_envelope_fidelity``.
    """
    lite = encode_lite(envelope)
    raw_wire = json.dumps(envelope)
    raw_compact = _compact(envelope)
    lite_text = _compact(lite)

    restored = decode_lite(lite)
    expected = _expected_roundtrip(envelope)

    return {
        "type": str(envelope.get("type", "chat")),
        "bytes_raw_wire": len(raw_wire),
        "bytes_raw_compact": len(raw_compact),
        "bytes_lite": len(lite_text),
        "tokens_raw_wire": count_tokens(raw_wire, encoder),
        "tokens_lite": count_tokens(lite_text, encoder),
        "roundtrip_envelope_fidelity": restored == expected,
    }


def _ratio(part: int, whole: int) -> float:
    """Return ``part / whole`` rounded to four places, or ``0.0`` when empty."""
    return round(part / whole, 4) if whole else 0.0


def summarize(
    trace: list[dict[str, Any]], encoder: Encoder | None, tokenizer: str
) -> dict[str, Any]:
    """Aggregate per-message measurements into a benchmark summary.

    Parameters
    ----------
    trace : list[dict[str, Any]]
        The fixed sequence of broadcast envelopes.
    encoder : Encoder or None
        Tokeniser passed through to :func:`measure_message`.
    tokenizer : str
        Label naming the token-counting method, recorded in the output.

    Returns
    -------
    dict[str, Any]
        Totals, ratios, per-type byte breakdown, and the worst-case fidelity
        flag over the whole trace.
    """
    measured = [measure_message(env, encoder) for env in trace]

    total_raw_wire = sum(m["bytes_raw_wire"] for m in measured)
    total_raw_compact = sum(m["bytes_raw_compact"] for m in measured)
    total_lite = sum(m["bytes_lite"] for m in measured)
    total_tokens_wire = sum(m["tokens_raw_wire"] for m in measured)
    total_tokens_lite = sum(m["tokens_lite"] for m in measured)

    by_type: dict[str, dict[str, int]] = defaultdict(
        lambda: {"count": 0, "bytes_raw_wire": 0, "bytes_lite": 0}
    )
    for m in measured:
        bucket = by_type[m["type"]]
        bucket["count"] += 1
        bucket["bytes_raw_wire"] += m["bytes_raw_wire"]
        bucket["bytes_lite"] += m["bytes_lite"]

    return {
        "messages": len(measured),
        "tokenizer": tokenizer,
        "bytes": {
            "raw_wire": total_raw_wire,
            "raw_compact": total_raw_compact,
            "lite": total_lite,
            "lite_vs_raw_wire_ratio": _ratio(total_lite, total_raw_wire),
            "lite_vs_raw_compact_ratio": _ratio(total_lite, total_raw_compact),
        },
        "tokens": {
            "raw_wire": total_tokens_wire,
            "lite": total_tokens_lite,
            "lite_vs_raw_wire_ratio": _ratio(total_tokens_lite, total_tokens_wire),
        },
        "roundtrip_envelope_fidelity": all(m["roundtrip_envelope_fidelity"] for m in measured),
        "by_type": {k: dict(v) for k, v in sorted(by_type.items())},
        "per_message": measured,
    }


def load_trace(path: Path) -> list[dict[str, Any]]:
    """Load a committed trace file of broadcast envelopes."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data)


def run(
    trace_path: Path = DEFAULT_TRACE, results_path: Path | None = DEFAULT_RESULTS
) -> dict[str, Any]:
    """Run the benchmark and, when given a path, write the results as JSON.

    Parameters
    ----------
    trace_path : pathlib.Path, optional
        Trace file to measure. Defaults to the committed sample session.
    results_path : pathlib.Path or None, optional
        Where to write the JSON summary; ``None`` skips writing.

    Returns
    -------
    dict[str, Any]
        The summary returned by :func:`summarize`, with the trace name added.
    """
    trace = load_trace(trace_path)
    encoder, tokenizer = get_encoder()
    summary = summarize(trace, encoder, tokenizer)
    summary["trace"] = trace_path.name
    if results_path is not None:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(summary, indent=2, sort_keys=True)
        results_path.write_text(rendered + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the benchmark, and print a short summary."""
    parser = argparse.ArgumentParser(description="Measure lite relay encoding savings.")
    parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args(argv)

    summary = run(args.trace, args.results)
    b = summary["bytes"]
    t = summary["tokens"]
    print(f"trace: {summary['trace']} ({summary['messages']} messages)")
    print(f"tokenizer: {summary['tokenizer']}")
    print(
        f"bytes raw_wire={b['raw_wire']} raw_compact={b['raw_compact']} "
        f"lite={b['lite']} "
        f"(lite is {b['lite_vs_raw_wire_ratio']:.0%} of raw_wire, "
        f"{b['lite_vs_raw_compact_ratio']:.0%} of raw_compact)"
    )
    print(
        f"tokens raw_wire={t['raw_wire']} lite={t['lite']} "
        f"(lite is {t['lite_vs_raw_wire_ratio']:.0%} of raw_wire)"
    )
    print(f"full-envelope roundtrip fidelity: {summary['roundtrip_envelope_fidelity']}")
    print(f"results written to {args.results}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
