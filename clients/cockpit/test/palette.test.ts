// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — command palette brain tests

import { describe, expect, it, vi } from "vitest";
import {
  buildCommands,
  matchCommands,
  PALETTE_SHOWN,
  sendOperatorMessage,
} from "../src/lib/palette";

describe("buildCommands", () => {
  it("carries the static head plus focus/inspect per agent and inspect/trace per task", () => {
    const commands = buildCommands(["a/one"], ["t1"]);
    expect(commands.map((command) => command.kind)).toEqual([
      "toggle-theme",
      "toggle-density",
      "toggle-travel",
      "clear-focus",
      "operator-message",
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

describe("sendOperatorMessage", () => {
  it("posts the body and maps sent / not-armed / refused / error", async () => {
    const sent = vi.fn<typeof fetch>().mockResolvedValue(new Response("{}", { status: 200 }));
    expect(await sendOperatorMessage("CEO", "hello", sent)).toEqual({ kind: "sent" });
    const [url, init] = sent.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/message");
    expect(JSON.parse(init.body as string)).toEqual({ to: "CEO", text: "hello" });

    expect(
      await sendOperatorMessage("a", "b", vi.fn<typeof fetch>().mockResolvedValue(new Response("nf", { status: 404 }))),
    ).toEqual({ kind: "not-armed" });
    expect(
      await sendOperatorMessage("a", "b", vi.fn<typeof fetch>().mockResolvedValue(new Response("rate limited\n", { status: 429 }))),
    ).toEqual({ kind: "refused", reason: "rate limited" });
    expect(
      await sendOperatorMessage("a", "b", vi.fn<typeof fetch>().mockResolvedValue(new Response("", { status: 503 }))),
    ).toEqual({ kind: "refused", reason: "dashboard returned 503" });
    expect(
      await sendOperatorMessage("a", "b", vi.fn<typeof fetch>().mockResolvedValue(new Response("<html>err</html>", { status: 500 }))),
    ).toEqual({ kind: "refused", reason: "dashboard returned 500" });
    expect(
      await sendOperatorMessage("a", "b", vi.fn<typeof fetch>().mockResolvedValue(new Response("x".repeat(200), { status: 400 }))),
    ).toEqual({ kind: "refused", reason: "dashboard returned 400" });
    expect(
      await sendOperatorMessage("a", "b", vi.fn<typeof fetch>().mockResolvedValue(new Response("old", { status: 501 }))),
    ).toEqual({ kind: "not-armed" });
    expect(
      await sendOperatorMessage("a", "b", vi.fn<typeof fetch>().mockRejectedValue(new Error("down"))),
    ).toEqual({ kind: "error", message: "down" });
    expect(
      await sendOperatorMessage("a", "b", vi.fn<typeof fetch>().mockRejectedValue("plain")),
    ).toEqual({ kind: "error", message: "plain" });
  });

  it("runs on its defaults against the global fetch, which fails visibly in tests", async () => {
    expect((await sendOperatorMessage("a", "b")).kind).toBe("error");
  });
});
