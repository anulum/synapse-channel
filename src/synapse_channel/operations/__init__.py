# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — catalogue of operations that vendor clients offer
"""Operations that a vendor's client offers on a host, and how well each is known.

:mod:`~synapse_channel.operations.vocabulary` says which operation names
exist; :mod:`~synapse_channel.operations.row` describes one operation of one
answering component with its native binding;
:mod:`~synapse_channel.operations.catalogue` holds the rows of a host and
decides which of them may run. :mod:`~synapse_channel.operations.spec` is the
description of the earlier schema, still read as an inventory that admits
nothing. Being listed grants no authority.
"""
