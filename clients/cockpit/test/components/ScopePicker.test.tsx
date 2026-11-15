// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — the scope selector, the panel's scope tag and the scope in the URL

import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ScopePicker } from "../../src/components/ScopePicker";
import { ScopeContext, ScopeProvider, ScopeTag } from "../../src/components/ScopeTag";
import { Hud } from "../../src/components/Hud";
import { useCockpitScope } from "../../src/hooks/useCockpitScope";
import { WHOLE_HUB, type CockpitScope } from "../../src/lib/scope";

const COUNTS = {
  tasksTotal: 7,
  agentsTotal: 4,
  tasksWithoutProject: 2,
  agentsWithoutNamespace: 1,
  namespaces: [{ namespace: "ALPHA", tasks: 2, agents: 1 }],
};
const ALPHA: CockpitScope = { kind: "project", namespace: "ALPHA" };

afterEach(() => {
  cleanup();
  history.replaceState(null, "", "/");
});

describe("ScopePicker", () => {
  it("offers each scope with its counts and reports the one chosen", async () => {
    const onChange = vi.fn();
    render(<ScopePicker scope={WHOLE_HUB} counts={COUNTS} onChange={onChange} />);
    const select = screen.getByRole("combobox", { name: "Scope: one project's part of the fleet" });

    expect([...select.querySelectorAll("option")].map((option) => option.textContent)).toEqual([
      "whole hub · 7 tasks · 4 agents",
      "no project · 2 tasks · 1 agents",
      "ALPHA · 2 tasks · 1 agents",
    ]);
    await userEvent.selectOptions(select, "project:ALPHA");
    expect(onChange).toHaveBeenCalledWith(ALPHA);
  });

  it("lists the declared groups under their own heading, each with its counts", async () => {
    const onChange = vi.fn();
    const groups = [{ group: "ships", tasks: 5, agents: 3 }];
    render(<ScopePicker scope={WHOLE_HUB} counts={{ ...COUNTS, groups }} onChange={onChange} />);
    const select = screen.getByRole("combobox", { name: "Scope: one project's part of the fleet" });
    const heading = select.querySelector("optgroup");

    expect(heading?.getAttribute("label")).toBe("Declared groups");
    expect(heading?.textContent).toBe("ships · 5 tasks · 3 agents");
    await userEvent.selectOptions(select, "group:ships");
    expect(onChange).toHaveBeenCalledWith({ kind: "group", name: "ships" });
  });

  it("marks an active scope and renders in the head-up display's slot", () => {
    render(
      <Hud
        kpis={[]}
        live
        stamp="—"
        scopeControl={<ScopePicker scope={ALPHA} counts={null} onChange={vi.fn()} />}
      />,
    );
    const select = screen.getByRole("combobox", { name: "Scope: one project's part of the fleet" });
    expect((select as HTMLSelectElement).value).toBe("project:ALPHA");
    expect(select.parentElement?.className).toContain("hud__scope--on");
  });
});

describe("ScopeTag", () => {
  it("says nothing for the whole hub", () => {
    const { container } = render(<ScopeTag />);
    expect(container.textContent).toBe("");
  });

  it("names the scope, the whole hub for an unscoped panel, and a panel's note", () => {
    render(
      <ScopeContext.Provider value={ALPHA}>
        <ScopeTag />
        <ScopeTag scoped={false} />
        <ScopeTag note="scope.auditTitle" />
      </ScopeContext.Provider>,
    );
    const tags = screen.getAllByText(/^scope: /u);
    expect(tags.map((tag) => tag.textContent)).toEqual([
      "scope: project:ALPHA",
      "scope: whole hub",
      "scope: project:ALPHA",
    ]);
    expect(tags[0]?.getAttribute("title")).toBeNull();
    expect(tags[1]?.getAttribute("title")).toBe(
      "This panel reads a feed the scope does not narrow; it shows the whole hub.",
    );
    expect(tags[2]?.getAttribute("title")).toBe(
      "Receipts are narrowed to the scope; operator actions show the whole hub.",
    );
  });

  it("names the whole hub on a panel that shows the rebuilt past while the cockpit travels", () => {
    const { rerender } = render(
      <ScopeProvider scope={ALPHA} travelling={false}>
        <ScopeTag replayed />
      </ScopeProvider>,
    );
    expect(screen.getByText(/^scope: /u).textContent).toBe("scope: project:ALPHA");
    rerender(
      <ScopeProvider scope={ALPHA} travelling>
        <ScopeTag replayed />
      </ScopeProvider>,
    );
    const tag = screen.getByText(/^scope: /u);
    expect(tag.textContent).toBe("scope: whole hub");
    expect(tag.getAttribute("title")).toBe(
      "This panel reads a feed the scope does not narrow; it shows the whole hub.",
    );
  });
});

describe("useCockpitScope", () => {
  it("reads the URL, writes one history entry per change and follows Back", () => {
    history.replaceState(null, "", "/?panel=fleet&scope=none");
    const { result } = renderHook(() => useCockpitScope());
    expect(result.current.scope).toEqual({ kind: "none" });

    act(() => result.current.setScope(ALPHA));
    expect(location.search).toBe("?panel=fleet&scope=project%3AALPHA");
    expect(result.current.scope).toEqual(ALPHA);
    const length = history.length;
    act(() => result.current.setScope(ALPHA));
    expect(history.length).toBe(length);

    act(() => {
      history.replaceState(null, "", "/?panel=fleet");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(result.current.scope).toEqual(WHOLE_HUB);
  });
});
