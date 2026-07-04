// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — post-mortem export parsing tests

import { describe, expect, it } from "vitest";
import { parseLogExport, readLogExportFile } from "../src/lib/postmortem";
import type { CockpitEvent } from "../src/types";

const EVENT: CockpitEvent = {
  seq: 42,
  ts: 1783.5,
  kind: "claim",
  lane: "claims",
  severity: 0.5,
  actor: "a/agent",
  label: "claimed t",
  taskId: "t",
  payload: { task_id: "t" },
};

function exportDoc(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    exported_at: "2026-07-04T10:00:00.000Z",
    provenance: "hub",
    query: {},
    window: null,
    count: 1,
    events: [EVENT],
    ...overrides,
  };
}

describe("parseLogExport", () => {
  it("round-trips an export document, payload included", () => {
    const parsed = parseLogExport(exportDoc());
    expect(parsed).not.toBeNull();
    expect(parsed?.provenance).toBe("hub");
    expect(parsed?.exportedAt).toBe("2026-07-04T10:00:00.000Z");
    expect(parsed?.count).toBe(1);
    expect(parsed?.events[0]).toEqual(EVENT);
  });

  it("refuses non-exports outright", () => {
    expect(parseLogExport(null)).toBeNull();
    expect(parseLogExport([EVENT])).toBeNull();
    expect(parseLogExport(exportDoc({ provenance: "guessed" }))).toBeNull();
    expect(parseLogExport(exportDoc({ events: "junk" }))).toBeNull();
  });

  it("drops malformed events without repairing them, and re-sorts newest first", () => {
    const older: CockpitEvent = { ...EVENT, seq: 7, payload: undefined as never };
    const { payload: _omit, ...olderNoPayload } = older;
    const parsed = parseLogExport(
      exportDoc({
        exported_at: 42,
        events: [
          olderNoPayload,
          { ...EVENT, kind: "not-a-kind" },
          { ...EVENT, lane: "not-a-lane" },
          { ...EVENT, seq: "not-a-number" },
          "junk",
          { ...EVENT, seq: 99, severity: "x", actor: 1, label: 2, taskId: 3, payload: [1] },
          EVENT,
        ],
      }),
    );
    expect(parsed?.events.map((event) => event.seq)).toEqual([99, 42, 7]);
    expect(parsed?.events[0]).toMatchObject({ severity: 0, actor: "", label: "", taskId: "" });
    expect(parsed?.events[0]?.payload).toBeUndefined();
    expect(parsed?.events[2]?.payload).toBeUndefined();
    expect(parsed?.exportedAt).toBe("");
    expect(parsed?.count).toBe(3);
  });
});

describe("readLogExportFile", () => {
  it("reads a valid export blob and refuses junk", async () => {
    const good = new Blob([JSON.stringify(exportDoc())], { type: "application/json" });
    expect((await readLogExportFile(good))?.count).toBe(1);
    const junk = new Blob(["not json {{"], { type: "application/json" });
    expect(await readLogExportFile(junk)).toBeNull();
    const wrongShape = new Blob([JSON.stringify({ hello: 1 })]);
    expect(await readLogExportFile(wrongShape)).toBeNull();
  });
});
