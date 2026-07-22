// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — persisted cockpit presentation preferences and log address

import { useCallback, useEffect, useState } from "react";

import { queryFromHash, queryToHash, type LogQuery } from "../lib/logQuery";
import { readPref, writePref } from "../lib/prefs";
import {
  applyTheme,
  persistTheme,
  resolveInitialTheme,
  toggledTheme,
  type Theme,
} from "../lib/theme";

export type CockpitDensity = "cozy" | "compact";

export interface CockpitPreferences {
  readonly focus: string;
  readonly density: CockpitDensity;
  readonly theme: Theme;
  readonly logQuery: LogQuery;
  readonly setFocus: (focus: string) => void;
  readonly toggleDensity: () => void;
  readonly toggleTheme: () => void;
  readonly setLogQuery: (query: LogQuery) => void;
}

function initialTheme(): Theme {
  return resolveInitialTheme(
    localStorage,
    matchMedia("(prefers-color-scheme: light)").matches,
  );
}

function initialLogQuery(): LogQuery {
  return queryFromHash(typeof location === "undefined" ? "" : location.hash);
}

/** Own persisted visual preferences and the shareable signal-log hash. */
export function useCockpitPreferences(): CockpitPreferences {
  const [focus, setFocusState] = useState<string>(
    () => readPref(localStorage, "cockpit-focus") ?? "",
  );
  const [density, setDensity] = useState<CockpitDensity>(() =>
    readPref(localStorage, "cockpit-density") === "compact" ? "compact" : "cozy",
  );
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [logQuery, setLogQueryState] = useState<LogQuery>(initialLogQuery);

  const setFocus = useCallback((next: string) => {
    setFocusState(next);
    writePref(localStorage, "cockpit-focus", next);
  }, []);

  const toggleDensity = useCallback(() => {
    setDensity((current) => {
      const next = current === "cozy" ? "compact" : "cozy";
      writePref(localStorage, "cockpit-density", next);
      return next;
    });
  }, []);

  useEffect(() => {
    if (density === "compact") document.documentElement.setAttribute("data-density", "compact");
    else document.documentElement.removeAttribute("data-density");
  }, [density]);

  useEffect(() => {
    applyTheme(theme, document.documentElement);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((current) => {
      const next = toggledTheme(current);
      persistTheme(next, localStorage);
      return next;
    });
  }, []);

  const setLogQuery = useCallback((query: LogQuery) => {
    setLogQueryState(query);
    const hash = queryToHash(query);
    const url = `${location.pathname}${location.search}${hash === "" ? "" : `#${hash}`}`;
    history.replaceState(history.state, "", url);
  }, []);

  return {
    focus,
    density,
    theme,
    logQuery,
    setFocus,
    toggleDensity,
    toggleTheme,
    setLogQuery,
  };
}
