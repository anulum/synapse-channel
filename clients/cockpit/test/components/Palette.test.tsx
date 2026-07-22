// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — command palette behaviour tests

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Palette } from "../../src/components/Palette";
import type { DashboardCapabilities } from "../../src/lib/access";
import { resetCockpitAuth, unlockCockpit } from "../../src/lib/auth";
import { buildCommands } from "../../src/lib/palette";

beforeEach(() => {
  sessionStorage.clear();
  resetCockpitAuth();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

const OPERATOR: DashboardCapabilities = {
  read: true,
  message_send: true,
  task_declare: true,
  task_update: true,
};
const COMMANDS = buildCommands(["quantum/worker"], ["t-1"], OPERATOR);

describe("Palette", () => {
  it("renders nothing while closed", () => {
    const { container } = render(
      <Palette open={false} commands={COMMANDS} onClose={() => {}} onRun={() => {}} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("contains no write DOM or search result when the catalogue is viewer-only", async () => {
    const commands = buildCommands(["quantum/worker"], ["t-1"], {
      read: true,
      message_send: false,
      task_declare: false,
      task_update: false,
    });
    render(<Palette open commands={commands} onClose={() => {}} onRun={() => {}} />);
    expect(document.querySelector('[class*="--write"]')).toBeNull();
    expect(screen.queryByText(/operator:/u)).toBeNull();
    await userEvent.type(screen.getByLabelText("Search commands"), "operator");
    expect(screen.getByText("no command matches")).toBeTruthy();
    await userEvent.keyboard("{Enter}");
    expect(screen.queryByLabelText("Message recipient")).toBeNull();
    expect(screen.queryByLabelText("Task id")).toBeNull();
  });

  it("ranks matches as typed and runs the chosen command from the keyboard", async () => {
    const onRun = vi.fn();
    const onClose = vi.fn();
    render(<Palette open commands={COMMANDS} onClose={onClose} onRun={onRun} />);
    const input = await screen.findByLabelText("Search commands");
    await userEvent.type(input, "focus quantum");
    await userEvent.keyboard("{Enter}");
    expect(onRun).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "focus-agent", subject: "quantum/worker" }),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("moves the cursor with arrows and states an empty match honestly", async () => {
    const onRun = vi.fn();
    render(<Palette open commands={COMMANDS} onClose={() => {}} onRun={onRun} />);
    const input = await screen.findByLabelText("Search commands");
    await userEvent.keyboard("{ArrowDown}{ArrowUp}{ArrowUp}");
    await userEvent.type(input, "no-such-command-anywhere");
    expect(screen.getByText("no command matches")).toBeTruthy();
  });

  it("closes on Escape even when focus left the input", async () => {
    const onClose = vi.fn();
    render(<Palette open commands={COMMANDS} onClose={onClose} onRun={() => {}} />);
    await screen.findByLabelText("Search commands");
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("composes a governed message and states the relay's outcome as a fact", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ action: "message", status: "undelivered", detail: "accepted; no live recipient (dead-lettered)", ok: true }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetcher);
    expect(unlockCockpit("operator-secret")).toBe(true);
    render(<Palette open commands={COMMANDS} onClose={() => {}} onRun={() => {}} />);
    await userEvent.click(screen.getByText("operator: send a message…"));
    await userEvent.type(screen.getByLabelText("Message recipient"), "ghost/agent");
    await userEvent.type(screen.getByLabelText("Message text"), "hello{Enter}");
    await waitFor(() =>
      expect(screen.getByText("relayed, not delivered — accepted; no live recipient (dead-lettered)")).toBeTruthy(),
    );
    expect(fetcher).toHaveBeenCalledWith(
      "/message",
      expect.objectContaining({
        method: "POST",
        headers: expect.any(Headers),
      }),
    );
    const headers = new Headers(fetcher.mock.calls[0]?.[1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer operator-secret");
    await userEvent.click(screen.getByText("back"));
    expect(screen.getByLabelText("Search commands")).toBeTruthy();
  });

  it("keeps send disabled until both fields are filled and sends from the button", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("nf", { status: 404 }));
    vi.stubGlobal("fetch", fetcher);
    render(<Palette open commands={COMMANDS} onClose={() => {}} onRun={() => {}} />);
    await userEvent.click(screen.getByText("operator: send a message…"));
    const send = screen.getByText("send");
    expect((send as HTMLButtonElement).disabled).toBe(true);
    await userEvent.type(screen.getByLabelText("Message recipient"), "CEO");
    await userEvent.type(screen.getByLabelText("Message text"), "status");
    expect((send as HTMLButtonElement).disabled).toBe(false);
    await userEvent.click(send);
    await waitFor(() =>
      expect(
        screen.getByText("operator write-path not armed on this dashboard (--operator)"),
      ).toBeTruthy(),
    );
  });

  it("opens the governed composer with a selected fleet peer prefilled", () => {
    render(
      <Palette
        open
        commands={COMMANDS}
        compose={{ to: "SYNAPSE-CHANNEL/claude-d1ae", nonce: 1 }}
        onClose={() => {}}
        onRun={() => {}}
      />,
    );
    expect((screen.getByLabelText("Message recipient") as HTMLInputElement).value).toBe(
      "SYNAPSE-CHANNEL/claude-d1ae",
    );
    expect(screen.getByText(/authorised and audited by the hub/u)).toBeTruthy();
  });

  it("opens focused task forms, carries live ids, returns to commands, and closes on Escape", async () => {
    const onClose = vi.fn();
    render(<Palette open commands={COMMANDS} onClose={onClose} onRun={() => {}} />);
    await userEvent.click(screen.getByText("operator: declare a task…"));
    expect(document.activeElement).toBe(screen.getByLabelText("Task id"));
    await userEvent.click(screen.getByRole("button", { name: "back" }));
    expect(screen.getByLabelText("Search commands")).toBeTruthy();

    await userEvent.click(screen.getByText("operator: update a task…"));
    const options = [...document.querySelectorAll<HTMLOptionElement>("#operator-task-ids option")];
    expect(options.map((option) => option.value)).toEqual(["t-1"]);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("closes from the veil click but not from inside the dialog", async () => {
    const onClose = vi.fn();
    render(<Palette open commands={COMMANDS} onClose={onClose} onRun={() => {}} />);
    await userEvent.click(screen.getByRole("dialog"));
    expect(onClose).not.toHaveBeenCalled();
    const veil = document.querySelector(".drawer-veil");
    expect(veil).not.toBeNull();
    await userEvent.click(veil as Element);
    expect(onClose).toHaveBeenCalled();
  });
});
