// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — governed cockpit operator-action client tests

import { describe, expect, it, vi } from "vitest";

import {
  declareOperatorTask,
  parseDependencyIds,
  parseOperatorOutcome,
  sendOperatorResponse,
  sendOperatorMessage,
  updateOperatorTask,
  validateTaskDeclaration,
  validateTaskUpdate,
} from "../src/lib/operatorActions";

function outcome(action: string, status: string, detail: string, ok: boolean, httpStatus: number): Response {
  return new Response(JSON.stringify({ action, status, detail, ok }), {
    status: httpStatus,
  });
}

function resolved(response: Response): ReturnType<typeof vi.fn<typeof fetch>> {
  return vi.fn<typeof fetch>().mockResolvedValue(response);
}

describe("operator outcome contract", () => {
  it("accepts only the expected strict document while tolerating action-specific fields", () => {
    expect(
      parseOperatorOutcome(
        {
          action: "task",
          status: "accepted",
          detail: "recorded",
          ok: true,
          id: "T-1",
        },
        "task",
      ),
    ).toEqual({
      action: "task",
      status: "accepted",
      detail: "recorded",
      ok: true,
    });
    expect(parseOperatorOutcome({ action: "message", status: "accepted", detail: "", ok: true }, "task")).toBeNull();
    expect(parseOperatorOutcome({ action: "task", status: "", detail: "", ok: true }, "task")).toBeNull();
    expect(parseOperatorOutcome({ action: "task", status: 7, detail: "", ok: true }, "task")).toBeNull();
    expect(parseOperatorOutcome({ action: "task", status: "accepted", detail: 7, ok: true }, "task")).toBeNull();
    expect(parseOperatorOutcome({ action: "task", status: "accepted", detail: "", ok: "yes" }, "task")).toBeNull();
    expect(parseOperatorOutcome(["task"], "task")).toBeNull();
    expect(parseOperatorOutcome(null, "task")).toBeNull();
  });

  it("parses dependencies and validates both task forms without contacting the hub", () => {
    expect(parseDependencyIds(" T-0, T-1\nT-0, , T-2 ")).toEqual(["T-0", "T-1", "T-2"]);
    expect(validateTaskDeclaration({ id: "", title: "Ship", dependsOn: [] })).toBe("Task id is required.");
    expect(validateTaskDeclaration({ id: "T-1", title: "  ", dependsOn: [] })).toBe("Task title is required.");
    expect(
      validateTaskDeclaration({
        id: " T-1 ",
        title: "Ship",
        dependsOn: ["T-1"],
      }),
    ).toBe("A task cannot depend on itself.");
    expect(
      validateTaskDeclaration({
        id: "T-1",
        title: "Ship",
        dependsOn: ["T-0"],
      }),
    ).toBeNull();
    expect(validateTaskUpdate({ id: "", status: "done" })).toBe("Task id is required.");
    expect(validateTaskUpdate({ id: "T-1", status: " ", note: "" })).toContain("status or note");
    expect(validateTaskUpdate({ id: "T-1", note: "recorded" })).toBeNull();
  });
});

describe("task actions", () => {
  it("declares a normalised dependent task and requires a valid outcome even on HTTP 200", async () => {
    const fetcher = resolved(outcome("task", "accepted", "task recorded", true, 200));
    expect(
      await declareOperatorTask(
        {
          id: " T-1 ",
          title: " Ship it ",
          dependsOn: [" T-0 ", "T-0", ""],
        },
        fetcher,
      ),
    ).toEqual({
      kind: "accepted",
      status: "accepted",
      detail: "task recorded",
    });
    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/task");
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({
      id: "T-1",
      title: "Ship it",
      depends_on: ["T-0"],
    });

    const malformed = await declareOperatorTask(
      { id: "T-2", title: "No document", dependsOn: [] },
      resolved(new Response("{}", { status: 200 })),
    );
    expect(malformed).toEqual({
      kind: "error",
      message: "dashboard returned 200 without a valid task outcome",
    });
  });

  it("updates only the supplied status/note fields and supports an explicit id", async () => {
    const fetcher = resolved(outcome("task_update", "accepted", "update recorded", true, 200));
    expect(await updateOperatorTask({ id: " T-1 ", note: " evidence " }, fetcher)).toEqual({
      kind: "accepted",
      status: "accepted",
      detail: "update recorded",
    });
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual({
      id: "T-1",
      note: "evidence",
    });
  });

  it.each([
    ["denied", false, 403, "denied"],
    ["rejected", false, 409, "rejected"],
    ["unreachable", false, 503, "unreachable"],
    ["accepted", true, 200, "accepted"],
  ] as const)("maps the %s server outcome without overriding hub authority", async (status, ok, http, kind) => {
    const result = await updateOperatorTask(
      { id: "T-1", status: "done" },
      resolved(outcome("task_update", status, `${status} detail`, ok, http)),
    );
    expect(result).toMatchObject({ kind });
  });

  it("distinguishes auth, arming, rate, reachability, malformed, and transport failures", async () => {
    const input = { id: "T-1", title: "Ship", dependsOn: [] };
    expect(await declareOperatorTask(input, resolved(new Response("auth", { status: 401 })))).toEqual({
      kind: "unauthorised",
    });
    expect(await declareOperatorTask(input, resolved(new Response("nf", { status: 404 })))).toEqual({
      kind: "not-armed",
    });
    expect(await declareOperatorTask(input, resolved(new Response("old", { status: 501 })))).toEqual({
      kind: "not-armed",
    });
    expect(
      await declareOperatorTask(
        input,
        resolved(
          new Response("operator rate limit exceeded\n", {
            status: 429,
          }),
        ),
      ),
    ).toEqual({
      kind: "rate-limited",
      detail: "operator rate limit exceeded",
    });
    expect(await declareOperatorTask(input, resolved(new Response("hub down\n", { status: 503 })))).toEqual({
      kind: "unreachable",
      detail: "hub down",
    });
    expect(await declareOperatorTask(input, resolved(new Response("<html>bad</html>", { status: 500 })))).toEqual({
      kind: "error",
      message: "dashboard returned 500 without a valid task outcome",
    });
    expect(await declareOperatorTask(input, vi.fn<typeof fetch>().mockRejectedValue(new Error("offline")))).toEqual({
      kind: "error",
      message: "offline",
    });
    expect(await declareOperatorTask(input, vi.fn<typeof fetch>().mockRejectedValue("plain failure"))).toEqual({
      kind: "error",
      message: "plain failure",
    });
  });

  it("returns validation failures before invoking fetch", async () => {
    const fetcher = vi.fn<typeof fetch>();
    expect(await declareOperatorTask({ id: "", title: "", dependsOn: [] }, fetcher)).toEqual({
      kind: "invalid",
      message: "Task id is required.",
    });
    expect(await updateOperatorTask({ id: "T-1" }, fetcher)).toEqual({
      kind: "invalid",
      message: "Add a status or note before updating the task.",
    });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("fails closed on an unknown false outcome and sanitises unsafe detail", async () => {
    expect(
      await updateOperatorTask(
        { id: "T-1", status: "done" },
        resolved(outcome("task_update", "mystery", "not accepted", false, 200)),
      ),
    ).toEqual({
      kind: "error",
      message: "dashboard reported unknown outcome 'mystery'",
    });
    expect(
      await updateOperatorTask(
        { id: "T-1", status: "done" },
        resolved(outcome("task_update", "denied", "<b>unsafe</b>", false, 403)),
      ),
    ).toEqual({ kind: "denied", detail: "dashboard returned 403" });
    expect(
      await updateOperatorTask(
        { id: "T-1", status: "done" },
        resolved(outcome("task_update", "accepted", "not authoritative", true, 503)),
      ),
    ).toEqual({
      kind: "error",
      message: "dashboard returned 503 with a success-shaped task_update outcome",
    });
  });
});

describe("message compatibility", () => {
  it("keeps delivered/undelivered facts while making malformed 200 responses fail closed", async () => {
    expect(
      await sendOperatorMessage(
        "CEO",
        "hello",
        resolved(outcome("message", "delivered", "delivered to CEO", true, 200)),
      ),
    ).toEqual({ kind: "sent", detail: "delivered to CEO" });
    expect(
      await sendOperatorMessage(
        "ghost",
        "hello",
        resolved(outcome("message", "undelivered", "dead-lettered", true, 200)),
      ),
    ).toEqual({ kind: "undelivered", detail: "dead-lettered" });
    expect(await sendOperatorMessage("a", "b", resolved(new Response("{}", { status: 200 })))).toEqual({
      kind: "error",
      message: "dashboard returned 200 without a valid message outcome",
    });
    expect(await sendOperatorMessage("a", "b", resolved(new Response("auth", { status: 401 })))).toEqual({
      kind: "refused",
      reason: "dashboard bearer was refused",
    });
    expect(
      await sendOperatorMessage("a", "b", resolved(outcome("message", "denied", "hub denied", false, 403))),
    ).toEqual({ kind: "refused", reason: "hub denied" });
    expect(await sendOperatorMessage("a", "b", resolved(new Response("nf", { status: 404 })))).toEqual({
      kind: "not-armed",
    });
    expect(await sendOperatorMessage("a", "b", resolved(new Response("slow", { status: 429 })))).toEqual({
      kind: "refused",
      reason: "rate limited: slow",
    });
  });
});

describe("semantic message responses", () => {
  it("posts an exact message sequence, normalised target, closed status, and optional note", async () => {
    const fetcher = resolved(outcome("message_response", "delivered", "semantic response delivered", true, 200));
    expect(
      await sendOperatorResponse(
        {
          messageSeq: 42,
          to: " ALPHA ",
          status: "needs_input",
          note: " Which revision? ",
        },
        fetcher,
      ),
    ).toEqual({
      kind: "accepted",
      status: "delivered",
      detail: "semantic response delivered",
    });
    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/message/respond");
    expect(JSON.parse(init.body as string)).toEqual({
      message_seq: 42,
      to: "ALPHA",
      status: "needs_input",
      note: "Which revision?",
    });
  });

  it("fails locally without an exact durable sequence or sender", async () => {
    const fetcher = vi.fn<typeof fetch>();
    expect(await sendOperatorResponse({ messageSeq: 0, to: "ALPHA", status: "acknowledged" }, fetcher)).toEqual({
      kind: "error",
      message: "Select a durable message before responding.",
    });
    expect(await sendOperatorResponse({ messageSeq: 3, to: " ", status: "completed" }, fetcher)).toEqual({
      kind: "error",
      message: "The referenced sender is unavailable.",
    });
    expect(fetcher).not.toHaveBeenCalled();
  });
});
