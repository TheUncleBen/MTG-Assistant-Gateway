# syntax=mirror.gcr.io/docker/dockerfile:1.7
# Forge (https://github.com/Card-Forge/forge, GPL-3.0), unmodified, as the gateway's internal
# simulation engine, plus a small job service (forge_service.py) that starts Forge's headless
# `sim` mode and reports results. Build context: the repository root.
#
# The release archive is downloaded at build time and checked against FORGE_SHA256. The image
# workflow reads these lines and passes FORGE_URL (the release asset's address). Before a
# local build, run: python3 docker/forge/scryfall_names.py docker/forge/scryfall-names.json
ARG FORGE_VERSION=2.0.15
ARG FORGE_ASSET=forge-installer-2.0.15.tar.bz2
ARG FORGE_SHA256=unpinned
# Bump when this image changes without a new Forge release. The image is tagged <FORGE_VERSION>-<FORGE_LOCAL>.
ARG FORGE_LOCAL=mag1

FROM mirror.gcr.io/library/eclipse-temurin:17-jre AS source
ARG FORGE_URL
ARG FORGE_ASSET
ARG FORGE_SHA256
RUN apt-get update && apt-get install -y --no-install-recommends bzip2 ca-certificates curl python3 \
 && rm -rf /var/lib/apt/lists/*
RUN test -n "$FORGE_URL" \
 && curl -fsSL --retry 3 -o "/tmp/$FORGE_ASSET" "$FORGE_URL" \
 && echo "$FORGE_SHA256  /tmp/$FORGE_ASSET" | sha256sum -c - \
 && mkdir -p /opt/forge && tar -xjf "/tmp/$FORGE_ASSET" -C /opt/forge && rm "/tmp/$FORGE_ASSET" \
 && ls /opt/forge/*jar-with-dependencies.jar
# scryfall-names.json is written by docker/forge/scryfall_names.py before the build (the image
# workflow does this), so the image knows cards Forge lists under another printed name.
COPY docker/forge/build_names.py docker/forge/forge_service.py docker/forge/scryfall-names.json /tmp/fb/
RUN cd /tmp/fb && python3 build_names.py /opt/forge /opt/forge-index/names.txt scryfall-names.json

FROM mirror.gcr.io/library/eclipse-temurin:17-jre
ARG FORGE_VERSION
ARG FORGE_LOCAL
ARG FORGE_URL
LABEL org.opencontainers.image.source="https://github.com/TheUncleBen/MTG-Assistant-Gateway" \
      org.opencontainers.image.title="mtg-assistant-forge" \
      org.opencontainers.image.description="Forge ${FORGE_VERSION} (unmodified, ${FORGE_URL}; source https://github.com/Card-Forge/forge) with the gateway's simulation job service" \
      org.opencontainers.image.version="${FORGE_VERSION}-${FORGE_LOCAL}" \
      org.opencontainers.image.licenses="GPL-3.0-only AND LicenseRef-PolyForm-Noncommercial-1.0.0"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/data \
    FORGE_HOME=/opt/forge FORGE_VERSION=${FORGE_VERSION} FORGE_PORT=8000
# util-linux provides setpriv, used by the entrypoint to drop to PUID:PGID.
RUN apt-get update && apt-get install -y --no-install-recommends python3 util-linux \
 && rm -rf /var/lib/apt/lists/* \
 && (userdel -r ubuntu 2>/dev/null || true) \
 && groupadd -g 1000 forge && useradd -u 1000 -g 1000 -M -d /data -s /usr/sbin/nologin forge \
 && mkdir -p /data && chown forge:forge /data
COPY --from=source /opt/forge /opt/forge
COPY --from=source /opt/forge-index /opt/forge-index
COPY docker/forge/forge_service.py /app/forge_service.py
COPY docker/forge/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/forge/NOTICE.md /opt/forge/NOTICE-mtg-assistant.md
WORKDIR /opt/forge
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=5 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8).status==200 else 1)"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python3", "/app/forge_service.py"]
