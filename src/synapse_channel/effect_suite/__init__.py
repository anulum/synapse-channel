# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the effect-safety suite: is an agent system's effect controlled?
"""The effect-safety suite, cases 1 to 4, in the cooperative profile.

The suite drives a system under test through the six verbs of the integrator
contract and reads the result itself from the system of record: a Git branch in
a disposable repository, and the hub's journal. It never takes an effect from
the system's own report. Each case variant ends in one signed result record.
"""
