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
import { afterEach, describe, expect, it, vi } from "vitest";

import { Palette } from "../../src/components/Palette";
import { buildCommands } from "../../src/lib/palette";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const COMMANDS = buildCommands(["quantum/claude"], ["t-1"]);

describe("Palette", () => {
  it("renders nothing while closed", () => {
    const { container } = render(
      <Palette open={false} commands={COMMANDS} onClose={() => {}} onRun={() => {}} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("ranks matches as typed and runs the chosen command from the keyboard", async () => {
    const onRun = vi.fn();
    const onClose = vi.fn();
    render(<Palette open commands={COMMANDS} onClose={onClose} onRun={onRun} />);
    const input = await screen.findByLabelText("Search commands");
    await userEvent.type(input, "focus quantum");
    await userEvent.keyboard("{Enter}");
    expect(onRun).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "focus-agent", subject: "quantum/claude" }),
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

  it("composes the one write and states the relay's outcome as a fact", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ action: "message", status: "undelivered", detail: "accepted; no live recipient (dead-lettered)", ok: true }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetcher);
    render(<Palette open commands={COMMANDS} onClose={() => {}} onRun={() => {}} />);
    await userEvent.click(screen.getByText("operator: send a message…"));
    await userEvent.type(screen.getByLabelText("Message recipient"), "ghost/agent");
    await userEvent.type(screen.getByLabelText("Message text"), "hello{Enter}");
    await waitFor(() =>
      expect(screen.getByText("relayed, not delivered — accepted; no live recipient (dead-lettered)")).toBeTruthy(),
    );
    expect(fetcher).toHaveBeenCalledWith("/message", expect.objectContaining({ method: "POST" }));
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
