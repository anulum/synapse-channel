# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — container image for the coordination hub

# Build the wheel in a throwaway stage so the runtime image carries no build tools.
FROM python:3.13-slim@sha256:c33f0bc4364a6881bed1ec0cc2665e6c53c87a43e774aaeab88e6f17af105e4f AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir build \
    && python -m build --wheel --outdir /dist

FROM python:3.13-slim@sha256:c33f0bc4364a6881bed1ec0cc2665e6c53c87a43e774aaeab88e6f17af105e4f
LABEL org.opencontainers.image.title="synapse-channel" \
      org.opencontainers.image.description="Local-first multi-agent coordination hub" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.source="https://github.com/anulum/synapse-channel"

# Run as an unprivileged user; persist the durable log under /data.
RUN useradd --create-home --uid 10001 synapse && mkdir /data && chown synapse /data
COPY --from=build /dist/*.whl /tmp/
RUN python -m pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl
USER synapse
WORKDIR /home/synapse
EXPOSE 8876
VOLUME ["/data"]

# A liveness probe so orchestrators can tell whether the hub accepts connections.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["synapse", "health"]

# Bind 0.0.0.0 so the port is reachable across the container boundary. When the
# port is published beyond the host, require a shared secret with --token (see
# docs/deployment.md); the hub warns when bound off-loopback without one.
ENTRYPOINT ["synapse"]
CMD ["hub", "--host", "0.0.0.0", "--port", "8876", \
     "--db", "/data/hub.db", "--relay-log", "/data/feed.ndjson"]
