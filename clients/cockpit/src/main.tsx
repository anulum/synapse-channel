// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — cockpit entry point

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
// Self-hosted typefaces (bundled woff2, no CDN): the cockpit renders
// identically offline and sends no request to any external origin.
import "@fontsource/space-grotesk/400.css";
import "@fontsource/space-grotesk/500.css";
import "@fontsource/space-grotesk/600.css";
import "@fontsource/space-grotesk/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "@fontsource/jetbrains-mono/700.css";
import "./styles/global.css";

const mount = document.getElementById("cockpit");
if (mount === null) throw new Error("cockpit mount node #cockpit is missing");

createRoot(mount).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
