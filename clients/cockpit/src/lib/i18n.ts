// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — typed, offline cockpit localisation

export const SUPPORTED_LOCALES = ["en", "sk"] as const;
export type CockpitLocale = (typeof SUPPORTED_LOCALES)[number];

export const LOCALE_PREFERENCE_KEY = "cockpit-locale";

const ENGLISH = {
  "app.accessChecking": "checking dashboard access…",
  "app.accessChanged": "Dashboard access changed; write controls were removed.",
  "auth.title": "Unlock cockpit",
  "auth.description": "This dashboard protects its live feeds. Paste the bearer provided for your dashboard principal; it stays in this tab's session storage only.",
  "auth.tokenLabel": "Dashboard bearer token",
  "auth.empty": "Paste the dashboard bearer token.",
  "auth.storageError": "Session storage is unavailable; the cockpit cannot retain this bearer safely.",
  "auth.submit": "unlock cockpit",
  "auth.safety": "The bearer is never placed in the URL, logs, local storage, or shell cache.",
  "hud.product": "channel · cockpit",
  "hud.commands": "commands",
  "hud.openCommands": "Open command palette",
  "hud.openGuide": "Open cockpit guide",
  "hud.guide": "guide",
  "hud.locale": "Interface language",
  "hud.focusPlaceholder": "focus identity…",
  "hud.focusLabel": "Focus the claims and board on one identity",
  "hud.focusTitle": "Narrow the claims board and task board to one identity's work",
  "hud.clearFocus": "Clear the focus lens",
  "hud.clear": "clear",
  "hud.toggleDensity": "Toggle display density",
  "hud.compact": "compact",
  "hud.cozy": "cozy",
  "hud.densityCompactTitle": "Tighten the rows for a big fleet",
  "hud.densityCozyTitle": "Relax the rows",
  "hud.switchLight": "Switch to light theme",
  "hud.switchDark": "Switch to dark theme",
  "hud.light": "light",
  "hud.dark": "dark",
  "hud.live": "live",
  "hud.stale": "stale",
  "hud.transport": "Live transport: {status}",
  "hud.transport.live": "stream",
  "hud.transport.fallback": "poll fallback",
  "hud.transport.gap": "gap detected",
  "hud.transport.connecting": "connecting",
  "hud.transport.reconnecting": "reconnecting",
  "hud.transport.stopped": "stopped",
  "hud.filterKpi": "Filter the signal log to {label}",
  "kpi.agents": "agents online",
  "kpi.claims": "claims held",
  "kpi.observations": "obs / min",
  "kpi.risk": "risk signals",
  "tab.attention": "attention",
  "tab.log": "signal log",
  "tab.fleet": "fleet",
  "tab.topology": "topology",
  "tab.metrics": "metrics",
  "tab.audit": "audit",
  "tab.incident": "incident",
  "tab.causality": "causality",
  "tabs.label": "Inspector",
  "tabs.clearWindow": "Clear the brushed window",
  "mobile.label": "Deck section",
  "mobile.signals": "signals",
  "mobile.claims": "claims",
  "mobile.board": "board",
  "mobile.roster": "roster",
  "mobile.reliability": "reliability",
  "guide.title": "Cockpit guide",
  "guide.context": "What am I looking at?",
  "guide.contextFor": "Guide for {panel}",
  "guide.searchLabel": "Search the cockpit guide",
  "guide.searchPlaceholder": "search evidence, actions, access…",
  "guide.close": "Close cockpit guide",
  "guide.noResults": "No guide topic matches this search.",
  "guide.localOnly": "Local guide · no query or usage telemetry leaves this browser",
  "guide.shortcut": "Press ? for guide · Ctrl/Command K for commands · Escape closes drawers",
  "guide.topic.orientation.title": "Read the cockpit",
  "guide.topic.orientation.summary": "Start with freshness, transport, attention, then exact retained evidence.",
  "guide.topic.orientation.body": "The HUD states whether data is live or stale and whether it arrived by stream or polling fallback. Attention identifies observable work; the signal log remains the orderable evidence record.",
  "guide.topic.limits.title": "Understand evidence limits",
  "guide.topic.limits.summary": "Absent, empty, stale, bounded, and complete are different claims.",
  "guide.topic.limits.body": "A retained-window cap does not prove that earlier evidence was dropped. An absent optional feed is not a healthy zero. Use the coverage strip and exact sequence range before drawing a completeness conclusion.",
  "guide.topic.actions.title": "Governed actions and receipts",
  "guide.topic.actions.summary": "A successful HTTP request is not proof of delivery or completion.",
  "guide.topic.actions.body": "Write controls appear only for the current capability. Read the exact outcome: accepted, delivered, undelivered, denied, rejected, rate-limited, and unreachable are distinct protocol evidence.",
  "guide.topic.shortcuts.title": "Keyboard and accessibility",
  "guide.topic.shortcuts.summary": "Operate every primary surface without a pointer.",
  "guide.topic.shortcuts.body": "Use Ctrl/Command K for commands, ? for this guide, Escape to close, and arrow keys plus Home/End across inspector tabs. Every graph keeps a textual evidence peer.",
  "guide.topic.troubleshooting.title": "Troubleshoot access and freshness",
  "guide.topic.troubleshooting.summary": "Separate rejected access, reconnecting transport, fallback, and absent feeds.",
  "guide.topic.troubleshooting.body": "A rejected bearer returns to the unlock boundary. reconnecting and gap detected trigger a fresh bootstrap. poll fallback keeps compatible authenticated feeds active during a sustained stream outage.",
  "guide.panel.attention.title": "Attention queue",
  "guide.panel.attention.body": "Deterministic evidence categories collect missing waiters, stale claims, conflicts, blocked tasks, failed routes, and pending approvals. They do not assign authority or invent a health score.",
  "guide.panel.log.title": "Signal log",
  "guide.panel.log.body": "This is the textual, orderable record of retained events. Filters and selected sequences remain URL-addressable; a retained range is evidence of scope, not a complete-history claim.",
  "guide.panel.fleet.title": "Fleet views",
  "guide.panel.fleet.body": "Web, matrix, project, timeline, and flow modes project only retained communication and claim evidence. Select a mark to open its textual route or event peer.",
  "guide.panel.topology.title": "Topology",
  "guide.panel.topology.body": "Topology joins exact identities, held tasks, claims, and imported federation posture. Quiet or absent is not inferred to mean offline.",
  "guide.panel.metrics.title": "Metrics",
  "guide.panel.metrics.body": "Metrics and session costs are bounded reports. Their feed state shows connecting, live, stale, absent, or failed instead of silently substituting zero.",
  "guide.panel.audit.title": "Audit",
  "guide.panel.audit.body": "Universal receipts and governed operator actions link request, decision, outcome, and durable evidence where served. Transport acknowledgement alone is not task completion.",
  "guide.panel.incident.title": "Incident workspace",
  "guide.panel.incident.body": "Draft scope, attach explicit evidence, add notes, and export a local JSON record. The export is an operator draft, not a signed hub receipt or audit bundle.",
  "guide.panel.causality.title": "Causality",
  "guide.panel.causality.body": "Trace a concrete task or sequence through recorded causes and effects. The view refuses to infer edges that are absent from retained evidence.",
} as const;

export type MessageKey = keyof typeof ENGLISH;
type Catalogue = Readonly<Record<MessageKey, string>>;

const SLOVAK: Catalogue = {
  "app.accessChecking": "overujem prístup k dashboardu…",
  "app.accessChanged": "Prístup k dashboardu sa zmenil; ovládacie prvky zápisu boli odstránené.",
  "auth.title": "Odomknúť cockpit",
  "auth.description": "Tento dashboard chráni živé dátové kanály. Vložte bearer pridelený vášmu dashboard principal; zostane iba v session storage tejto karty.",
  "auth.tokenLabel": "Dashboard bearer token",
  "auth.empty": "Vložte dashboard bearer token.",
  "auth.storageError": "Session storage nie je dostupné; cockpit nedokáže bearer bezpečne uchovať.",
  "auth.submit": "odomknúť cockpit",
  "auth.safety": "Bearer sa nikdy nevkladá do URL, logov, local storage ani shell cache.",
  "hud.product": "kanál · cockpit",
  "hud.commands": "príkazy",
  "hud.openCommands": "Otvoriť paletu príkazov",
  "hud.openGuide": "Otvoriť príručku cockpit-u",
  "hud.guide": "príručka",
  "hud.locale": "Jazyk rozhrania",
  "hud.focusPlaceholder": "zamerať identitu…",
  "hud.focusLabel": "Zamerať claims a board na jednu identitu",
  "hud.focusTitle": "Zúžiť claims board a task board na prácu jednej identity",
  "hud.clearFocus": "Zrušiť zameranie identity",
  "hud.clear": "zrušiť",
  "hud.toggleDensity": "Prepnúť hustotu zobrazenia",
  "hud.compact": "kompaktne",
  "hud.cozy": "vzdušne",
  "hud.densityCompactTitle": "Zmenšiť riadky pre veľkú flotilu",
  "hud.densityCozyTitle": "Zväčšiť rozostupy riadkov",
  "hud.switchLight": "Prepnúť na svetlú tému",
  "hud.switchDark": "Prepnúť na tmavú tému",
  "hud.light": "svetlá",
  "hud.dark": "tmavá",
  "hud.live": "živé",
  "hud.stale": "zastarané",
  "hud.transport": "Živý transport: {status}",
  "hud.transport.live": "stream",
  "hud.transport.fallback": "poll fallback",
  "hud.transport.gap": "gap detected",
  "hud.transport.connecting": "connecting",
  "hud.transport.reconnecting": "reconnecting",
  "hud.transport.stopped": "stopped",
  "hud.filterKpi": "Filtrovať signal log podľa {label}",
  "kpi.agents": "agenti online",
  "kpi.claims": "držané claims",
  "kpi.observations": "pozorovania / min",
  "kpi.risk": "rizikové signály",
  "tab.attention": "pozornosť",
  "tab.log": "signal log",
  "tab.fleet": "flotila",
  "tab.topology": "topológia",
  "tab.metrics": "metriky",
  "tab.audit": "audit",
  "tab.incident": "incident",
  "tab.causality": "kauzalita",
  "tabs.label": "Inšpektor",
  "tabs.clearWindow": "Zrušiť označené časové okno",
  "mobile.label": "Sekcia plochy",
  "mobile.signals": "signály",
  "mobile.claims": "claims",
  "mobile.board": "board",
  "mobile.roster": "zostava",
  "mobile.reliability": "spoľahlivosť",
  "guide.title": "Príručka cockpit-u",
  "guide.context": "Na čo sa pozerám?",
  "guide.contextFor": "Príručka pre {panel}",
  "guide.searchLabel": "Hľadať v príručke cockpit-u",
  "guide.searchPlaceholder": "hľadať evidence, actions, access…",
  "guide.close": "Zavrieť príručku cockpit-u",
  "guide.noResults": "Tomuto vyhľadávaniu nezodpovedá žiadna téma.",
  "guide.localOnly": "Lokálna príručka · žiadny dopyt ani telemetria neopustí tento prehliadač",
  "guide.shortcut": "? otvorí príručku · Ctrl/Command K otvorí príkazy · Escape zatvorí drawer",
  "guide.topic.orientation.title": "Ako čítať cockpit",
  "guide.topic.orientation.summary": "Začnite čerstvosťou, transportom, pozornosťou a až potom presnou retained evidence.",
  "guide.topic.orientation.body": "HUD uvádza, či sú dáta živé alebo zastarané a či prišli cez stream alebo polling fallback. Attention označuje pozorovateľnú prácu; signal log zostáva zoraditeľným evidence záznamom.",
  "guide.topic.limits.title": "Rozumieť hraniciam evidence",
  "guide.topic.limits.summary": "Absent, empty, stale, bounded a complete sú rozdielne tvrdenia.",
  "guide.topic.limits.body": "Dosiahnutý limit retained window nedokazuje, že staršie evidence bolo zahodené. Chýbajúci voliteľný feed nie je zdravá nula. Pred tvrdením o úplnosti skontrolujte coverage pás a presný rozsah sequence.",
  "guide.topic.actions.title": "Riadené actions a receipts",
  "guide.topic.actions.summary": "Úspešný HTTP request nie je dôkazom delivery ani completion.",
  "guide.topic.actions.body": "Ovládanie zápisu sa zobrazí iba pri aktuálnej capability. Čítajte presný outcome: accepted, delivered, undelivered, denied, rejected, rate-limited a unreachable sú rozdielne protocol evidence.",
  "guide.topic.shortcuts.title": "Klávesnica a prístupnosť",
  "guide.topic.shortcuts.summary": "Každú hlavnú plochu možno ovládať bez ukazovacieho zariadenia.",
  "guide.topic.shortcuts.body": "Ctrl/Command K otvorí príkazy, ? túto príručku a Escape ju zavrie. Medzi kartami inšpektora sa pohybujte šípkami a Home/End. Každý graf má textový evidence ekvivalent.",
  "guide.topic.troubleshooting.title": "Riešenie prístupu a čerstvosti",
  "guide.topic.troubleshooting.summary": "Odlišujte rejected access, reconnecting transport, fallback a absent feeds.",
  "guide.topic.troubleshooting.body": "Odmietnutý bearer vráti rozhranie na unlock boundary. reconnecting a gap detected spustia nový bootstrap. poll fallback udrží kompatibilné autentizované feedy počas dlhšieho výpadku streamu.",
  "guide.panel.attention.title": "Front pozornosti",
  "guide.panel.attention.body": "Deterministické evidence kategórie spájajú missing waiters, stale claims, conflicts, blocked tasks, failed routes a pending approvals. Neprideľujú autoritu ani nevymýšľajú health score.",
  "guide.panel.log.title": "Signal log",
  "guide.panel.log.body": "Toto je textový, zoraditeľný záznam retained events. Filtre a vybrané sequence zostávajú v URL; retained rozsah je dôkazom scope, nie tvrdením o úplnej histórii.",
  "guide.panel.fleet.title": "Pohľady flotily",
  "guide.panel.fleet.body": "Web, matrix, project, timeline a flow premietajú iba retained communication a claim evidence. Výber značky otvorí jej textový route alebo event ekvivalent.",
  "guide.panel.topology.title": "Topológia",
  "guide.panel.topology.body": "Topológia spája presné identity, held tasks, claims a importovaný federation posture. Z ticha alebo neprítomnosti nevyvodzuje offline stav.",
  "guide.panel.metrics.title": "Metriky",
  "guide.panel.metrics.body": "Metrics a session costs sú bounded reports. Stav feedu rozlišuje connecting, live, stale, absent a failed namiesto tichého dosadenia nuly.",
  "guide.panel.audit.title": "Audit",
  "guide.panel.audit.body": "Universal receipts a governed operator actions prepájajú request, decision, outcome a durable evidence, ak sú dostupné. Transport acknowledgement samo osebe nie je task completion.",
  "guide.panel.incident.title": "Incident workspace",
  "guide.panel.incident.body": "Definujte scope, pripojte explicit evidence, pridajte poznámky a exportujte lokálny JSON záznam. Export je operator draft, nie signed hub receipt ani audit bundle.",
  "guide.panel.causality.title": "Kauzalita",
  "guide.panel.causality.body": "Sledujte konkrétny task alebo sequence cez zaznamenané causes a effects. Pohľad nevyvodzuje edges, ktoré v retained evidence chýbajú.",
};

export const CATALOGUES: Readonly<Record<CockpitLocale, Catalogue>> = {
  en: ENGLISH,
  sk: SLOVAK,
};

function normaliseLocale(candidate: string): CockpitLocale | null {
  const base = candidate.trim().toLowerCase().split("-")[0];
  return base === "en" || base === "sk" ? base : null;
}

export function resolveLocale(
  search: string,
  stored: string | null,
  browserLanguages: readonly string[],
): CockpitLocale {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const explicit = normaliseLocale(params.get("lang") ?? "");
  if (explicit !== null) return explicit;
  const preferred = normaliseLocale(stored ?? "");
  if (preferred !== null) return preferred;
  for (const candidate of browserLanguages) {
    const locale = normaliseLocale(candidate);
    if (locale !== null) return locale;
  }
  return "en";
}

export function searchWithLocale(search: string, locale: CockpitLocale): string {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  params.set("lang", locale);
  return `?${params.toString()}`;
}

export function formatMessage(
  locale: CockpitLocale,
  key: MessageKey,
  values: Readonly<Record<string, string | number>> = {},
): string {
  return formatCatalogueMessage(CATALOGUES[locale], key, values);
}

/** Runtime fallback protects partial future catalogues while compile-time parity stays strict today. */
export function formatCatalogueMessage(
  catalogue: Partial<Catalogue>,
  key: MessageKey,
  values: Readonly<Record<string, string | number>> = {},
): string {
  const template = catalogue[key] ?? ENGLISH[key];
  return template.replace(/\{([A-Za-z0-9_]+)\}/gu, (match, name: string) =>
    Object.hasOwn(values, name) ? String(values[name]) : match,
  );
}
