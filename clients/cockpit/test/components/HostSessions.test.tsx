// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — component integration with a real Python dashboard
// @vitest-environment jsdom
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtempSync, writeFileSync, chmodSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";
import { HostSessions, formatRuntime } from "../../src/components/HostSessions";
import { CockpitI18nProvider, useCockpitI18n } from "../../src/context/CockpitI18n";
import { formatMessage, SUPPORTED_LOCALES } from "../../src/lib/i18n";
import { resetCockpitAuth, unlockCockpit } from "../../src/lib/auth";

const directory = mkdtempSync(join(tmpdir(), "host-sessions-component-"));
const grants = join(directory, "grants.json");
const token = "disposable-host-component-test-token";
let child: ChildProcessWithoutNullStreams;
let base: string;
const networkFetch = globalThis.fetch;
function grant(allowed: boolean, paths = false, context = false): void {
  writeFileSync(grants, JSON.stringify({ version: 1, observers: allowed
    ? { compatibility: { paths, context } } : {} }));
  chmodSync(grants, 0o600);
}
beforeAll(async () => {
  grant(true);
  const program = `
import ctypes, os, sys
from synapse_channel.dashboard import start_dashboard_server
context_root = os.path.dirname(sys.argv[1])
rollout = open(os.path.join(context_root, "rollout-component-12345678-1234-1234-1234-123456789abc.jsonl"), "w+")
assert ctypes.CDLL(None).prctl(15, b"codex", 0, 0, 0) == 0
server = start_dashboard_server(
    host="127.0.0.1", port=0, uri="ws://127.0.0.1:1", name="component-test",
    token=None, ready_timeout=0.1, response_timeout=0.1, refresh_seconds=2,
    allow_non_loopback=False, dashboard_token=sys.argv[2],
    host_sessions_access_file=sys.argv[1], host_session_pids=(os.getpid(),),
    host_session_tmux_socket="/nonexistent/component-test.sock",
    host_session_context_root=context_root,
)
print(server.url("/"), flush=True)
try:
    sys.stdin.read()
finally:
    server.close()
    rollout.close()
`;
  child = spawn("../../.venv/bin/python", ["-c", program, grants, token]);
  base = await new Promise<string>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("dashboard startup timed out")), 5000);
    child.once("error", reject);
    child.once("exit", (code) => { if (code !== null && code !== 0) reject(new Error("dashboard exited")); });
    child.stdout.once("data", (chunk: Buffer) => { clearTimeout(timer); resolve(chunk.toString().trim()); });
  });
  // jsdom has no browser URL resolution; transport still crosses real loopback HTTP.
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) =>
    networkFetch(new URL(String(input), base), init));
}, 10000);
afterEach(() => {
  cleanup(); resetCockpitAuth(); grant(true); localStorage.clear();
  history.replaceState(null, "", "/cockpit/");
  document.documentElement.removeAttribute("lang");
});
afterAll(async () => {
  vi.unstubAllGlobals();
  if (child && child.exitCode === null) {
    const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
    child.stdin.end();
    const timeout = setTimeout(() => child.kill("SIGKILL"), 5000);
    await exited;
    clearTimeout(timeout);
  }
  rmSync(directory, { recursive: true, force: true });
});
it("renders real metadata, filters it and clears it after live grant revocation", async () => {
  unlockCockpit(token);
  render(<HostSessions revision={1} />);
  expect(await screen.findByText("Read-only host observation", {}, { timeout: 5000 })).toBeTruthy();
  expect(screen.getByText(/OS state is not agent activity/)).toBeTruthy();
  expect(screen.getByText(/^Observation age: \d+ s$/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Copy context ID" })).toBeNull();
  const summary = document.querySelector("summary");
  expect(summary?.textContent).toContain("PID");
  expect(summary?.textContent).toMatch(/ · runtime (\d+d )?\d{2}:\d{2}:\d{2} · /);
  expect(summary?.textContent).not.toContain("runtime unknown");
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox", { name: "Filter processes" }), "no-such-process");
  expect(screen.getByText("No matching rows.")).toBeTruthy();
  await user.clear(screen.getByRole("textbox", { name: "Filter processes" }));
  const control = document.querySelector("summary") as HTMLElement;
  await user.click(control);
  expect(document.querySelector("details")?.open).toBe(true);
  control.focus();
  await user.keyboard("{Escape}");
  expect(document.querySelector("details")?.open).toBe(false);
  expect(document.activeElement).toBe(control);
  await user.type(screen.getByRole("textbox", { name: "Filter processes" }), "old-private-filter");
  grant(false);
  await waitFor(() => expect(screen.getByText("Your credential has no host observation grant.")).toBeTruthy(),
    { timeout: 5000 });
  expect(document.querySelector("summary")).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
  grant(true);
  await screen.findByText("Read-only host observation", {}, { timeout: 5000 });
  expect((screen.getByRole("textbox", { name: "Filter processes" }) as HTMLInputElement).value).toBe("");
}, 15000);
it("formats runtime as a whole-second clock with a day prefix past 24 h", () => {
  expect(formatRuntime(0)).toBe("00:00:00");
  expect(formatRuntime(59.9)).toBe("00:00:59");
  expect(formatRuntime(3661)).toBe("01:01:01");
  expect(formatRuntime(90061.5)).toBe("1d 01:01:01");
  expect(formatRuntime(-5)).toBe("00:00:00");
});
it("credential revision hides the old observation immediately", async () => {
  unlockCockpit(token);
  const view = render(<HostSessions revision={2} />);
  await screen.findByText("Read-only host observation", {}, { timeout: 5000 });
  grant(false);
  view.rerender(<HostSessions revision={3} />);
  expect(document.querySelector("summary")).toBeNull();
  await screen.findByText("Your credential has no host observation grant.", {}, { timeout: 5000 });
});

it("renders per-field evidence from real granted metadata", async () => {
  grant(true, true, true);
  unlockCockpit(token);
  render(<HostSessions revision={1} />);
  await screen.findByText("Read-only host observation", {}, { timeout: 5000 });
  const user = userEvent.setup();
  const summary = document.querySelector("summary") as HTMLElement;
  await user.click(summary);
  expect(screen.getByText("Working directory").nextElementSibling?.textContent).toMatch(/\(observed\)$/);
  const started = screen.getByText("Process start (boot time + start ticks, ±1 s)").nextElementSibling?.textContent;
  expect(started).toMatch(/\(observed\)$/);
  expect(started).not.toContain("unknown");
  expect(screen.getByText("Context ID").nextElementSibling?.textContent).toContain("12345678-1234-1234-1234-123456789abc (observed)");
  expect(screen.getByRole("button", { name: "Copy context ID" })).toBeTruthy();
  grant(true, true, false);
  await waitFor(() => expect(screen.queryByRole("button", { name: "Copy context ID" })).toBeNull(),
    { timeout: 5000 });
  expect(screen.getByText("Context ID").nextElementSibling?.textContent).toBe("not granted (not_requested)");
});

it.each(SUPPORTED_LOCALES)("renders the real feed in %s with translated controls", async (locale) => {
  history.replaceState(null, "", `/cockpit/?lang=${locale}`);
  unlockCockpit(token);
  render(<CockpitI18nProvider><HostSessions revision={1} /></CockpitI18nProvider>);
  await screen.findByText(formatMessage(locale, "host.live"), {}, { timeout: 5000 });
  expect(screen.getByRole("region", { name: formatMessage(locale, "host.title") })).toBeTruthy();
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox", { name: formatMessage(locale, "host.filter") }), "no-match");
  expect(screen.getByText(formatMessage(locale, "host.noMatches"))).toBeTruthy();
  expect(document.documentElement.lang).toBe(locale);
});

function LanguageSwitch(): React.JSX.Element {
  const { setLocale } = useCockpitI18n();
  return <button type="button" onClick={() => setLocale("sk")}>Slovenčina</button>;
}

it("language changes retain the active filter and observation", async () => {
  history.replaceState(null, "", "/cockpit/?lang=en");
  unlockCockpit(token);
  render(<CockpitI18nProvider><LanguageSwitch /><HostSessions revision={1} /></CockpitI18nProvider>);
  await screen.findByText(formatMessage("en", "host.live"), {}, { timeout: 5000 });
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox", { name: "Filter processes" }), "no-match");
  await user.click(screen.getByRole("button", { name: "Slovenčina" }));
  expect(screen.getByText(formatMessage("sk", "host.live"))).toBeTruthy();
  expect(screen.getByText(formatMessage("sk", "host.noMatches"))).toBeTruthy();
  expect((screen.getByRole("textbox", { name: formatMessage("sk", "host.filter") }) as HTMLInputElement).value).toBe("no-match");
});
