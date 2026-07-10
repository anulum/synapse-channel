// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — shared unit-test environment configuration

import { configure } from "@testing-library/react";

// This workstation runs several agents and instrumented (coverage) suites at
// once, and the default one-second async-utility timeout reads scheduler load
// as test failure. Five seconds keeps the assertions honest — a healthy render
// resolves in milliseconds — without letting a loaded box flake the deck.
configure({ asyncUtilTimeout: 5_000 });
