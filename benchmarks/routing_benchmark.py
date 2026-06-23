# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — measure how the task-class router classifies a prompt set
"""Measure the task-class router's decisions on a fixed prompt set.

Routing only pays off if the cheap classes catch the trivial requests and the
heavy class is reserved for the genuinely hard ones. This benchmark replays a
committed set of prompts through :func:`synapse_channel.client.routing.classify` and
reports the class distribution and the per-prompt decision — all exact and
reproducible, so the routing policy can be reviewed and tuned from data.

It also verifies routing dispatch: a :class:`~synapse_channel.client.routing.TieredChatClient`
wired to tagging stub backends must send each prompt to the backend for its
class. Backend *latency* is deliberately not measured here — the ``slm`` and
``heavy`` tiers need a live model server, so timing them is not reproducible
offline and is left to a live run; the committed numbers are the deterministic
routing decisions only.

Run with ``python benchmarks/routing_benchmark.py``; results are written to
``benchmarks/results/routing_benchmark.json``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from synapse_channel.client.routing import TaskClass, TieredChatClient, classify

BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_TRACE = BENCHMARK_DIR / "traces" / "routing_prompts.json"
DEFAULT_RESULTS = BENCHMARK_DIR / "results" / "routing_benchmark.json"

CLASSES = (TaskClass.RULE, TaskClass.SLM, TaskClass.HEAVY)


class _TagBackend:
    """A stub backend whose reply is its own class tag, to prove dispatch."""

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        """Return the class tag this backend was registered under."""
        return self.tag


def load_prompts(path: Path) -> list[str]:
    """Load a committed list of prompts from a JSON file."""
    return [str(item) for item in json.loads(path.read_text(encoding="utf-8"))]


def summarize(prompts: list[str]) -> dict[str, Any]:
    """Classify every prompt and verify tiered dispatch.

    Parameters
    ----------
    prompts : list[str]
        The fixed prompt set to route.

    Returns
    -------
    dict[str, Any]
        The class distribution, per-prompt decisions, and whether a tiered
        client dispatched each prompt to the backend for its class.
    """
    by_prompt = [{"prompt": prompt, "class": classify(prompt)} for prompt in prompts]
    distribution = {cls: sum(1 for item in by_prompt if item["class"] == cls) for cls in CLASSES}

    tiered = TieredChatClient({cls: _TagBackend(cls) for cls in CLASSES})
    routing_verified = all(
        tiered.generate(system_prompt="", user_prompt=prompt) == classify(prompt)
        for prompt in prompts
    )

    return {
        "prompts": len(prompts),
        "distribution": distribution,
        "routing_verified": routing_verified,
        "by_prompt": by_prompt,
    }


def run(
    trace_path: Path = DEFAULT_TRACE, results_path: Path | None = DEFAULT_RESULTS
) -> dict[str, Any]:
    """Run the benchmark and, when given a path, write the results as JSON.

    Parameters
    ----------
    trace_path : pathlib.Path, optional
        Prompt file to classify. Defaults to the committed prompt set.
    results_path : pathlib.Path or None, optional
        Where to write the JSON summary; ``None`` skips writing.

    Returns
    -------
    dict[str, Any]
        The summary, with the trace name added.
    """
    prompts = load_prompts(trace_path)
    summary = summarize(prompts)
    summary["trace"] = trace_path.name
    if results_path is not None:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(summary, indent=2, sort_keys=True)
        results_path.write_text(rendered + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the benchmark, and print a short summary."""
    parser = argparse.ArgumentParser(description="Measure task-class routing decisions.")
    parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args(argv)

    summary = run(args.trace, args.results)
    dist = summary["distribution"]
    print(f"trace: {summary['trace']} ({summary['prompts']} prompts)")
    print(f"distribution: rule={dist['rule']} slm={dist['slm']} heavy={dist['heavy']}")
    print(f"routing dispatch verified: {summary['routing_verified']}")
    print(f"results written to {args.results}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
