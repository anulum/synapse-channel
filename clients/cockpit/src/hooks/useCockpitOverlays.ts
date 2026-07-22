// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — transient cockpit overlays and command entry

import type { RefObject } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { DashboardCapabilities } from "../lib/access";
import { lostWriteCapability } from "../lib/access";
import { buildCommands, type Command } from "../lib/palette";
import type { CockpitSelection } from "../lib/workspace";

export interface InspectedSubject {
  readonly kind: "agent" | "task";
  readonly id: string;
}

export interface TraceRequest {
  readonly subject: string;
  readonly nonce: number;
}

export interface PaletteComposeRequest {
  readonly to: string;
  readonly nonce: number;
}

interface OverlayOptions {
  readonly blocked: boolean;
  readonly authBlocked: boolean;
  readonly capabilities: DashboardCapabilities;
  readonly accessChangedMessage: string;
  readonly agents: readonly string[];
  readonly tasks: readonly string[];
  readonly setSelection: (selection: CockpitSelection | null) => void;
  readonly setFocus: (focus: string) => void;
  readonly toggleTheme: () => void;
  readonly toggleDensity: () => void;
  readonly toggleTravel: () => void;
}

export interface CockpitOverlayController {
  readonly paletteOpen: boolean;
  readonly guideOpen: boolean;
  readonly setupOpen: boolean;
  readonly paletteCompose: PaletteComposeRequest | null;
  readonly inspected: InspectedSubject | null;
  readonly traceRequest: TraceRequest | undefined;
  readonly accessNotice: string;
  readonly commands: readonly Command[];
  readonly commandTrigger: RefObject<HTMLButtonElement | null>;
  readonly guideTrigger: RefObject<HTMLButtonElement | null>;
  readonly setupTrigger: RefObject<HTMLButtonElement | null>;
  readonly inspectAgent: (identity: string) => void;
  readonly inspectTask: (taskId: string) => void;
  readonly closeInspection: () => void;
  readonly messagePeer: (identity: string) => void;
  readonly openPalette: () => void;
  readonly closePalette: () => void;
  readonly openGuide: () => void;
  readonly closeGuide: () => void;
  readonly openSetup: () => void;
  readonly closeSetup: () => void;
  readonly requestTrace: (subject: string) => void;
  readonly runCommand: (command: Command) => void;
}

/** Own modal visibility, keyboard entry, inspection, and safe capability-loss closure. */
export function useCockpitOverlays({
  blocked,
  authBlocked,
  capabilities,
  accessChangedMessage,
  agents,
  tasks,
  setSelection,
  setFocus,
  toggleTheme,
  toggleDensity,
  toggleTravel,
}: OverlayOptions): CockpitOverlayController {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  const [paletteCompose, setPaletteCompose] = useState<PaletteComposeRequest | null>(null);
  const [inspected, setInspected] = useState<InspectedSubject | null>(null);
  const [traceRequest, setTraceRequest] = useState<TraceRequest | undefined>(undefined);
  const [accessNotice, setAccessNotice] = useState("");
  const commandTrigger = useRef<HTMLButtonElement | null>(null);
  const guideTrigger = useRef<HTMLButtonElement | null>(null);
  const setupTrigger = useRef<HTMLButtonElement | null>(null);
  const previousCapabilities = useRef(capabilities);

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      const target = event.target;
      const typing =
        target instanceof HTMLElement &&
        (target.isContentEditable || ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName));
      if (!blocked && event.key.toLowerCase() === "k" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        setGuideOpen(false);
        setSetupOpen(false);
        setPaletteCompose(null);
        setPaletteOpen((current) => !current);
      } else if (!blocked && !typing && event.key === "?") {
        event.preventDefault();
        setPaletteOpen(false);
        setPaletteCompose(null);
        setSetupOpen(false);
        setGuideOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [blocked]);

  useEffect(() => {
    const previous = previousCapabilities.current;
    previousCapabilities.current = capabilities;
    if (authBlocked || !lostWriteCapability(previous, capabilities)) return;
    setPaletteOpen(false);
    setPaletteCompose(null);
    setAccessNotice(accessChangedMessage);
    commandTrigger.current?.focus();
  }, [accessChangedMessage, authBlocked, capabilities]);

  useEffect(() => {
    if (!blocked) return;
    setPaletteOpen(false);
    setPaletteCompose(null);
    setGuideOpen(false);
    setSetupOpen(false);
    setInspected(null);
    setTraceRequest(undefined);
  }, [blocked]);

  const inspectAgent = useCallback((identity: string) => {
    setSelection({ kind: "agent", id: identity });
    setInspected({ kind: "agent", id: identity });
  }, [setSelection]);

  const inspectTask = useCallback((taskId: string) => {
    setSelection({ kind: "task", id: taskId });
    setInspected({ kind: "task", id: taskId });
  }, [setSelection]);

  const closeInspection = useCallback(() => setInspected(null), []);

  const messagePeer = useCallback((identity: string) => {
    setPaletteCompose((current) => ({ to: identity, nonce: (current?.nonce ?? 0) + 1 }));
    setPaletteOpen(true);
  }, []);

  const openPalette = useCallback(() => {
    setPaletteCompose(null);
    setPaletteOpen(true);
  }, []);

  const closePalette = useCallback(() => {
    setPaletteOpen(false);
    setPaletteCompose(null);
  }, []);

  const openGuide = useCallback(() => {
    setPaletteOpen(false);
    setPaletteCompose(null);
    setSetupOpen(false);
    setGuideOpen(true);
  }, []);

  const closeGuide = useCallback(() => {
    setGuideOpen(false);
    guideTrigger.current?.focus();
  }, []);

  const openSetup = useCallback(() => {
    setPaletteOpen(false);
    setPaletteCompose(null);
    setGuideOpen(false);
    setSetupOpen(true);
  }, []);

  const closeSetup = useCallback(() => {
    setSetupOpen(false);
    setupTrigger.current?.focus();
  }, []);

  const requestTrace = useCallback((subject: string) => {
    setTraceRequest((current) => ({ subject, nonce: (current?.nonce ?? 0) + 1 }));
  }, []);

  const commands = useMemo(
    () => buildCommands(agents, tasks, capabilities),
    [agents, capabilities, tasks],
  );

  const runCommand = useCallback((command: Command) => {
    if (command.kind === "focus-agent") setFocus(command.subject);
    else if (command.kind === "inspect-agent") inspectAgent(command.subject);
    else if (command.kind === "inspect-task") inspectTask(command.subject);
    else if (command.kind === "trace-task") requestTrace(command.subject);
    else if (command.kind === "toggle-theme") toggleTheme();
    else if (command.kind === "toggle-density") toggleDensity();
    else if (command.kind === "toggle-travel") toggleTravel();
    else if (command.kind === "clear-focus") setFocus("");
  }, [inspectAgent, inspectTask, requestTrace, setFocus, toggleDensity, toggleTheme, toggleTravel]);

  return {
    paletteOpen,
    guideOpen,
    setupOpen,
    paletteCompose,
    inspected,
    traceRequest,
    accessNotice,
    commands,
    commandTrigger,
    guideTrigger,
    setupTrigger,
    inspectAgent,
    inspectTask,
    closeInspection,
    messagePeer,
    openPalette,
    closePalette,
    openGuide,
    closeGuide,
    openSetup,
    closeSetup,
    requestTrace,
    runCommand,
  };
}
