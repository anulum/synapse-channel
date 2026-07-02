# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — installed-version benchmark suite
"""Benchmark suite runnable against the installed package.

:mod:`~synapse_channel.benchmark.probes` measures the production surfaces the
package actually exercises — durable event-store writes and replay, the lite
relay encoding, and real WebSocket round-trips against an in-process hub —
and :mod:`~synapse_channel.benchmark.scorecard` wraps the measurements in the
host context (load, CPU, governor, isolation label) that makes a number
honest. The ``synapse benchmark`` command (see
:mod:`synapse_channel.cli_benchmark`) drives both.

The deeper committed harnesses under the repository's ``benchmarks/``
directory remain the source of the documented numbers; this suite is the
installable scorecard for *your* machine and *your* installed version.
"""

from __future__ import annotations
