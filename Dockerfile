# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS build
WORKDIR /build
COPY pyproject.toml README.md LICENSE constraints.txt ./
COPY src ./src
COPY scripts/fetch_ocr_assets.py ./scripts/
# Self-hosted OCR engine for /scan (pinned, checksummed; see the script). Needs network at build time.
RUN python3 scripts/fetch_ocr_assets.py
RUN pip install --no-cache-dir --upgrade pip wheel \
 && pip wheel --no-cache-dir --wheel-dir /wheels -c constraints.txt .

FROM python:3.12-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/TheUncleBen/MTG-Assistant-Gateway" \
      org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    MTG_DATA_DIR=/data MTG_BACKUP_DIR=/backups MTG_LISTEN_PORT=8080
# util-linux provides setpriv, used by the entrypoint to drop to PUID:PGID.
RUN apt-get update && apt-get install -y --no-install-recommends util-linux ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -g 1000 mtg && useradd -u 1000 -g 1000 -M -d /data -s /usr/sbin/nologin mtg \
 && mkdir -p /data /backups && chown mtg:mtg /data /backups
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
COPY plugin /usr/share/mtg-gateway/plugin
# The Android app, when the release build produced one (docker/app-dist is empty otherwise; see docs/ANDROID.md).
COPY docker/app-dist /usr/share/mtg-gateway/app
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh
EXPOSE 8080
VOLUME ["/data", "/backups"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status==200 else 1)"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["mtg-gateway", "serve"]
