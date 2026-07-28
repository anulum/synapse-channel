# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — container image for the coordination hub

# Build the wheel in a throwaway stage so the runtime image carries no build tools.
FROM python:3.13-slim@sha256:c33f0bc4364a6881bed1ec0cc2665e6c53c87a43e774aaeab88e6f17af105e4f AS build
ARG SOURCE_DATE_EPOCH=0
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
WORKDIR /src
COPY .github/requirements/requirements-container-build.txt /tmp/requirements-container-build.txt
COPY pyproject.toml README.md ./
COPY LICENSE NOTICE.md ./
COPY LICENSES ./LICENSES
COPY src ./src
RUN python -m pip install --no-cache-dir --no-compile --no-deps \
        --only-binary=:all: --require-hashes \
        -r /tmp/requirements-container-build.txt \
    && python -m build --wheel --no-isolation --outdir /dist

FROM python:3.13-slim@sha256:c33f0bc4364a6881bed1ec0cc2665e6c53c87a43e774aaeab88e6f17af105e4f
LABEL org.opencontainers.image.title="synapse-channel" \
      org.opencontainers.image.description="Local-first multi-agent coordination hub" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.source="https://github.com/anulum/synapse-channel"

# Run as an unprivileged user; persist the durable log under /data.
RUN useradd --create-home --uid 10001 synapse && mkdir /data && chown synapse /data
COPY .github/requirements/requirements-container.txt /tmp/requirements-container.txt
COPY --from=build /dist/*.whl /tmp/
RUN python -m pip install --no-cache-dir --no-compile --no-deps \
        --only-binary=:all: --require-hashes \
        -r /tmp/requirements-container.txt \
    && python -m pip install --no-cache-dir --no-compile --no-deps --no-index /tmp/*.whl \
    && rm -f /tmp/*.whl /tmp/requirements-container.txt
USER synapse
WORKDIR /home/synapse
EXPOSE 8876
VOLUME ["/data"]

# A liveness probe so orchestrators can tell whether the hub accepts connections.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["synapse", "health", "--uri", "ws://127.0.0.1:8876"]

# Bind 0.0.0.0 so the port is reachable across the container boundary; an
# in-container loopback bind would leave published ports dead. The bind is
# fail-closed: without --token the exposure guard raises InsecureBindError and
# refuses to start, unless --insecure-off-loopback explicitly accepts a
# host-guarded publish (the shipped docker-compose.yml publishes loopback-only).
# See SECURITY.md "Container image bind posture" and docs/deployment.md.
ENTRYPOINT ["synapse"]
CMD ["hub", "--host", "0.0.0.0", "--port", "8876", \
     "--advertised-host", "127.0.0.1:8876", \
     "--db", "/data/hub.db", "--relay-log", "/data/feed.ndjson"]
