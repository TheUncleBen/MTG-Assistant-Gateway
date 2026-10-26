# syntax=mirror.gcr.io/docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
# The base image and the Dockerfile frontend come from Google's public mirror of Docker Hub (same images,
# same digests) because anonymous pulls from Docker Hub are rate-limited per address, and shared CI
# runners hit that limit (0.7.10). CI also signs in to Docker Hub when the owner set DOCKERHUB_USERNAME
# and DOCKERHUB_TOKEN (docs/DEPLOY.md).
# Every image is pinned by digest (the tag stays so people can read which version it is), so a rebuild
# uses exactly the image that was pinned. To move to a newer one, take the digest that
# `docker buildx imagetools inspect mirror.gcr.io/library/python:3.12-slim-bookworm` prints.
# The gateway's dependencies as a requirements list, so the step that builds their wheels below is
# cached until the dependencies themselves change (pyproject.toml also changes with every version).
FROM mirror.gcr.io/library/python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS requirements
COPY pyproject.toml /
RUN python3 -c "import tomllib; print('\n'.join(tomllib.load(open('/pyproject.toml', 'rb'))['project']['dependencies']))" \
  > /requirements.txt

FROM mirror.gcr.io/library/python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS build
WORKDIR /build
# The steps that change least come first, so a release that only changes the gateway's own code
# reuses the cached OCR download and dependency wheels.
COPY scripts/fetch_ocr_assets.py ./scripts/
# Self-hosted OCR engine for /scan (pinned, checksummed; see the script). Needs network at build time.
RUN python3 scripts/fetch_ocr_assets.py
COPY constraints.txt ./
COPY --from=requirements /requirements.txt ./
# pip and wheel pinned too, so the wheels are built the same way each time.
RUN pip install --no-cache-dir pip==26.2.1 wheel==0.48.0 \
 && pip wheel --no-cache-dir --wheel-dir /wheels -c constraints.txt -r requirements.txt
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# The scanner's own scripts and stylesheet are served gzipped when a .gz sits beside them (scan/routes.py).
RUN find src/mtg_gateway/scan/static -maxdepth 1 \( -name '*.js' -o -name '*.css' \) -exec gzip -9 -k -n {} + \
 && pip wheel --no-cache-dir --no-deps --wheel-dir /wheels -c constraints.txt .

FROM mirror.gcr.io/library/python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
LABEL org.opencontainers.image.source="https://github.com/TheUncleBen/MTG-Assistant-Gateway" \
      org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    MTG_DATA_DIR=/data MTG_BACKUP_DIR=/backups
# util-linux provides setpriv, used by the entrypoint to drop to PUID:PGID.
RUN apt-get update && apt-get install -y --no-install-recommends util-linux ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -g 1000 mtg && useradd -u 1000 -g 1000 -M -d /data -s /usr/sbin/nologin mtg \
 && mkdir -p /data /backups && chown mtg:mtg /data /backups
# The wheels are mounted for the install rather than copied, so they never become an image layer.
# --no-index: only those wheels are installed; nothing is fetched from the package index here.
RUN --mount=type=bind,from=build,source=/wheels,target=/wheels pip install --no-cache-dir --no-index /wheels/*.whl
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
