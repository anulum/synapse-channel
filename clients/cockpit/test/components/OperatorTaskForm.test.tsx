// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — governed operator task form behaviour

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OperatorTaskForm } from "../../src/components/OperatorTaskForm";
import { resetCockpitAuth, unlockCockpit } from "../../src/lib/auth";

function outcome(
  action: string,
  status: string,
  detail: string,
  ok: boolean,
  httpStatus: number,
): Response {
  return new Response(JSON.stringify({ action, status, detail, ok }), { status: httpStatus });
}

beforeEach(() => {
  sessionStorage.clear();
  resetCockpitAuth();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("OperatorTaskForm", () => {
  it("validates and declares a normalised dependent task with bearer auth", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(outcome("task", "accepted", "task recorded", true, 200));
    vi.stubGlobal("fetch", fetcher);
    expect(unlockCockpit("operator-secret")).toBe(true);
    const onBack = vi.fn();
    render(<OperatorTaskForm mode="declare" taskIds={[]} onBack={onBack} />);

    const taskId = screen.getByLabelText("Task id");
    expect(document.activeElement).toBe(taskId);
    await userEvent.click(screen.getByRole("button", { name: "declare task" }));
    expect((await screen.findByRole("status")).textContent).toContain("Task id is required");
    expect(fetcher).not.toHaveBeenCalled();

    await userEvent.type(taskId, "T-1");
    await userEvent.type(screen.getByLabelText("Task title"), "Ship cockpit actions");
    await userEvent.type(screen.getByLabelText(/Dependencies/u), "T-1");
    await userEvent.click(screen.getByRole("button", { name: "declare task" }));
    expect((await screen.findByRole("status")).textContent).toContain("cannot depend on itself");
    expect(fetcher).not.toHaveBeenCalled();

    await userEvent.clear(screen.getByLabelText(/Dependencies/u));
    await userEvent.type(screen.getByLabelText(/Dependencies/u), "T-0, T-0");
    await userEvent.click(screen.getByRole("button", { name: "declare task" }));
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain("accepted — task recorded"),
    );
    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/task");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer operator-secret");
    expect(JSON.parse(String(init.body))).toEqual({
      id: "T-1",
      title: "Ship cockpit actions",
      depends_on: ["T-0"],
    });

    await userEvent.click(screen.getByRole("button", { name: "back" }));
    expect(onBack).toHaveBeenCalledOnce();
  });

  it("populates live board ids, validates an update, and submits it from Enter", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(outcome("task_update", "accepted", "update recorded", true, 200));
    vi.stubGlobal("fetch", fetcher);
    expect(unlockCockpit("operator-secret")).toBe(true);
    render(<OperatorTaskForm mode="update" taskIds={["T-1", "T-2", "T-1"]} onBack={() => {}} />);

    const taskId = screen.getByLabelText("Task id");
    expect(document.activeElement).toBe(taskId);
    expect(
      [...document.querySelectorAll<HTMLOptionElement>("#operator-task-ids option")].map(
        (option) => option.value,
      ),
    ).toEqual(["T-1", "T-2"]);
    await userEvent.type(taskId, "EXPLICIT-TASK");
    await userEvent.keyboard("{Enter}");
    expect((await screen.findByRole("status")).textContent).toContain("Add a status or note");
    expect(fetcher).not.toHaveBeenCalled();

    await userEvent.type(screen.getByLabelText(/Progress note/u), "evidence recorded{Enter}");
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain("accepted — update recorded"),
    );
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual({
      id: "EXPLICIT-TASK",
      note: "evidence recorded",
    });
  });

  it.each([
    [outcome("task", "accepted", "recorded", true, 200), "accepted — recorded"],
    [outcome("task", "denied", "ACL refused", false, 403), "denied: ACL refused"],
    [outcome("task", "rejected", "cycle", false, 409), "rejected: cycle"],
    [outcome("task", "unreachable", "hub down", false, 503), "unreachable: hub down"],
    [new Response("auth", { status: 401 }), "dashboard bearer refused"],
    [new Response("nf", { status: 404 }), "operator write-path not armed"],
    [new Response("slow down", { status: 429 }), "rate limited: slow down"],
  ])("states every governed server boundary without claiming success", async (response, expected) => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(response));
    expect(unlockCockpit("operator-secret")).toBe(true);
    render(<OperatorTaskForm mode="declare" taskIds={[]} onBack={() => {}} />);
    await userEvent.type(screen.getByLabelText("Task id"), "T-1");
    await userEvent.type(screen.getByLabelText("Task title"), "Ship");
    await userEvent.click(screen.getByRole("button", { name: "declare task" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain(expected));
  });
});
