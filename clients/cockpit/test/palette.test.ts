// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — command palette brain tests

import { describe, expect, it } from "vitest";
import { buildCommands, matchCommands, PALETTE_SHOWN } from "../src/lib/palette";

describe("buildCommands", () => {
  it("carries the static head plus focus/inspect per agent and inspect/trace per task", () => {
    const commands = buildCommands(["a/one"], ["t1"]);
    expect(commands.map((command) => command.kind)).toEqual([
      "toggle-theme",
      "toggle-density",
      "toggle-travel",
      "clear-focus",
      "operator-message",
      "operator-task-declare",
      "operator-task-update",
      "focus-agent",
      "inspect-agent",
      "inspect-task",
      "trace-task",
    ]);
    expect(commands.find((command) => command.id === "focus:a/one")?.subject).toBe("a/one");
  });
});

describe("matchCommands", () => {
  const commands = buildCommands(["alpha/agent", "beta/agent"], ["task-alpha"]);

  it("shows the static head on an empty query and caps the list", () => {
    expect(matchCommands(commands, "").length).toBeLessThanOrEqual(PALETTE_SHOWN);
    expect(matchCommands(commands, "")[0]?.kind).toBe("toggle-theme");
    const many = buildCommands(
      Array.from({ length: 30 }, (_, index) => `p/agent-${index}`),
      [],
    );
    expect(matchCommands(many, "agent")).toHaveLength(PALETTE_SHOWN);
  });

  it("ranks prefix over word-start over substring over keywords", () => {
    const ranked = matchCommands(commands, "trace");
    expect(ranked[0]?.kind).toBe("trace-task");
    const wordStart = matchCommands(commands, "alpha");
    expect(wordStart.some((command) => command.id === "focus:alpha/agent")).toBe(true);
    // keywords-only match: "causality" appears only in trace keywords
    expect(matchCommands(commands, "causality")[0]?.kind).toBe("trace-task");
    expect(matchCommands(commands, "zzz-nothing")).toEqual([]);
  });
});
