# syntax=docker/dockerfile:1.7
# Mystic Forge at a pinned upstream commit, as an internal research and
# simulation service. Build context: the repository root.
#
# Upstream code is unchanged except for the patches in
# docker/mystic-forge/patches/, applied in order at build time. Each patch
# says why it exists; drop it when upstream carries the fix.

# Source of truth for the pin. The image workflow reads these lines.
ARG MF_REPO=https://github.com/Kautiontape/mystic-forge.git
ARG MF_COMMIT=dd82cd1449645862a2e309ae70df2fb7c3a2a839
ARG MF_VERSION=1.3.2
# Bump when the patch set changes. The image is tagged <MF_VERSION>-<MF_LOCAL>.
# Also bump it when the base image digest below changes (a Dependabot update): a
# published tag is never rebuilt, so a new base reaches the stack only under a new tag.
ARG MF_LOCAL=mag2

# Base image pinned by digest (the tag is kept for reading).
FROM python:3.14-slim@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2 AS source
ARG MF_REPO
ARG MF_COMMIT
ARG MF_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY docker/mystic-forge/patches/ /patches/
# Fetch exactly the pinned commit, refuse to build anything else, then apply
# the local patches (git apply fails the build if any does not apply cleanly).
RUN git init -q . \
 && git remote add origin "$MF_REPO" \
 && git fetch -q --depth 1 origin "$MF_COMMIT" \
 && git checkout -q FETCH_HEAD \
 && test "$(git rev-parse HEAD)" = "$MF_COMMIT" \
 && test "$(tr -d '[:space:]' < VERSION)" = "$MF_VERSION" \
 && for p in /patches/*.patch; do git apply --verbose "$p"; done \
 && rm -rf .git

FROM python:3.14-slim@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2
ARG MF_REPO
ARG MF_COMMIT
ARG MF_VERSION
ARG MF_LOCAL
LABEL org.opencontainers.image.source="https://github.com/TheUncleBen/MTG-Assistant-Gateway" \
      org.opencontainers.image.title="mtg-assistant-mysticforge" \
      org.opencontainers.image.description="Mystic Forge ${MF_VERSION} (${MF_REPO}@${MF_COMMIT}) with local patches ${MF_LOCAL}" \
      org.opencontainers.image.version="${MF_VERSION}-${MF_LOCAL}" \
      org.opencontainers.image.licenses="MIT"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    MYSTIC_FORGE_NO_INGEST=1 MYSTIC_FORGE_NTFY_OFF=1 \
    MYSTIC_FORGE_DB=/data/mystic_forge.db MYSTIC_FORGE_CR=/data/cr_cache.txt \
    MYSTIC_FORGE_PORT=8000
# util-linux provides setpriv, used by the entrypoint to drop to PUID:PGID.
RUN apt-get update && apt-get install -y --no-install-recommends util-linux ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -g 1000 mf && useradd -u 1000 -g 1000 -M -d /data -s /usr/sbin/nologin mf \
 && mkdir -p /data && chown mf:mf /data
WORKDIR /app
COPY docker/mystic-forge/constraints.txt /tmp/constraints.txt
COPY --from=source /src/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c /tmp/constraints.txt && rm /tmp/constraints.txt
# Same files upstream's own Dockerfile copies, plus its licence.
COPY --from=source /src/VERSION /src/server.py /src/LICENSE ./
COPY --from=source /src/mystic_forge ./mystic_forge
COPY docker/mystic-forge/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8).status==200 else 1)"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "server.py"]
