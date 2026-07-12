// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — session bearer unlock veil behaviour

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthVeil } from "../../src/components/AuthVeil";

afterEach(cleanup);

describe("AuthVeil", () => {
  it("names the refusal, rejects an empty token, and submits a non-empty bearer", async () => {
    const onUnlock = vi.fn().mockReturnValue(true);
    render(<AuthVeil reason="The previous bearer was refused." onUnlock={onUnlock} />);
    expect(screen.getByRole("heading", { name: "Unlock cockpit" })).toBeTruthy();
    expect(screen.queryByText("Unlock operator cockpit")).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("previous bearer");

    const input = screen.getByLabelText("Dashboard bearer token");
    await userEvent.type(input, "   ");
    await userEvent.click(screen.getByText("unlock cockpit"));
    expect(screen.getByRole("alert").textContent).toContain("Paste the dashboard bearer");
    expect(onUnlock).not.toHaveBeenCalled();

    await userEvent.clear(input);
    await userEvent.type(input, "current-token");
    await userEvent.click(screen.getByText("unlock cockpit"));
    expect(onUnlock).toHaveBeenCalledWith("current-token");
    expect((input as HTMLInputElement).value).toBe("");
    expect(screen.getByRole("alert").textContent).toContain("previous bearer");
  });

  it("keeps the veil closed when session storage cannot retain the bearer", async () => {
    render(<AuthVeil reason={null} onUnlock={() => false} />);
    await userEvent.type(screen.getByLabelText("Dashboard bearer token"), "token");
    await userEvent.click(screen.getByText("unlock cockpit"));
    expect(screen.getByRole("alert").textContent).toContain("Session storage is unavailable");
  });
});
