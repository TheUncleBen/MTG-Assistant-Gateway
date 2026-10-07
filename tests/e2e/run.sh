#!/usr/bin/env bash
# End-to-end environment for the gateway on a real (single-node) Docker Swarm.
#
# What it does, in the order docs/DEPLOY.md gives the owner:
#   1. overlay network           docker network create --driver overlay --attachable mtg-gateway
#   2. storage dirs for the pinned node
#   3. Authentik: provider + application + group bindings + test users (authentik_setup.py)
#   4. the three Docker secrets, with the exact commands from DEPLOY.md step 4
#   6. the stack, deployed from deploy/portainer-stack.yml with stack.env-style variables
#   7. an nginx edge standing in for Nginx Proxy Manager (tests/e2e/stacks/edge-stack.yml)
#   8. the DEPLOY.md verification curls, then the pytest suite in this directory
#
# Usage:  tests/e2e/run.sh up      build images, deploy everything, configure Authentik
#         tests/e2e/run.sh test    run the pytest suite against the deployed stack
#         tests/e2e/run.sh down    remove stacks, secrets and generated files
#         tests/e2e/run.sh all     up + test + down (what CI runs)
#
# Environment knobs (all optional):
#   MTG_IMAGE/MTG_TAG, MF_IMAGE/MF_TAG   images to deploy (default: build locally as mtg-gateway:e2e, mtg-assistant-mysticforge:e2e)
#   E2E_AUTHENTIK_IMAGE                  default ghcr.io/goauthentik/server:2025.6.4
#   E2E_SKIP_BUILD=1                     do not build the two images
#   E2E_DOCKER_BUILD_ARGS            extra flags for every docker build (the sandbox needs --network=host)
#   E2E_NO_HOSTS=1                       do not edit /etc/hosts (then point mtg.e2e.test and auth.e2e.test at 127.0.0.1 yourself)
#   MTG_ARCHIDEKT_BASE                   Archidekt API base for the gateway (default: the local mock in the edge stack)
#   E2E_SWARM_DNSRR=1                    switch the gateway stack's services to dnsrr (sandboxes without IPVS)
#   E2E_SUBNET                           subnet of the test overlay network (default 11.111.0.0/24, see step_network)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
GEN="$HERE/.generated"
export MTG_NETWORK_NAME="${MTG_NETWORK_NAME:-mtg-gateway}"
export MTG_PUBLIC_URL="https://mtg.e2e.test"
AUTH_URL="https://auth.e2e.test"
NODE="$(docker info --format '{{.Name}}')"
export MTG_IMAGE="${MTG_IMAGE:-mtg-gateway}" MTG_TAG="${MTG_TAG:-e2e}"
export MF_IMAGE="${MF_IMAGE:-mtg-assistant-mysticforge}" MF_TAG="${MF_TAG:-e2e}"
export E2E_HTTPS_PORT="${E2E_HTTPS_PORT:-443}"
# The Archidekt mock (stacks/edge-stack.yml) serves tests/fake_archidekt.py from this checkout.
export E2E_REPO_TESTS="$REPO/tests"
# Never send the test hostnames through an outbound proxy.
export no_proxy="${no_proxy:-}${no_proxy:+,}mtg.e2e.test,auth.e2e.test" NO_PROXY="${NO_PROXY:-}${NO_PROXY:+,}mtg.e2e.test,auth.e2e.test"

log() { printf '\n==> %s\n' "$*"; }

ensure_swarm() {
  if [ "$(docker info --format '{{.Swarm.LocalNodeState}}')" != "active" ]; then
    log "docker swarm init"
    docker swarm init --advertise-addr 127.0.0.1 >/dev/null
  fi
}

step_network() {
  # DEPLOY.md step 1
  # The subnet is the one departure from the guide's command: the gateway only accepts client
  # metadata documents from hosts that resolve to a public address (SSRF guard), so the test
  # network uses a public range and the CIMD mock in it passes that check. Nothing is routed there.
  docker network inspect "$MTG_NETWORK_NAME" >/dev/null 2>&1 || {
    log "DEPLOY.md 1: docker network create --driver overlay --attachable $MTG_NETWORK_NAME (test subnet ${E2E_SUBNET:-11.111.0.0/24})"
    docker network create --driver overlay --attachable --subnet "${E2E_SUBNET:-11.111.0.0/24}" "$MTG_NETWORK_NAME"
  }
}

step_certs() {
  mkdir -p "$GEN"
  [ -f "$GEN/cimd.crt" ] && return
  [ -f "$GEN/edge.crt" ] && { step_scryfall_cert; return; }
  log "generating a test CA and an edge certificate for mtg.e2e.test and auth.e2e.test"
  openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=MTG Assistant Gateway e2e test CA" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout "$GEN/ca.key" -out "$GEN/ca.crt" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj "/CN=mtg.e2e.test" \
    -keyout "$GEN/edge.key" -out "$GEN/edge.csr" 2>/dev/null
  printf 'subjectAltName=DNS:mtg.e2e.test,DNS:auth.e2e.test\n' > "$GEN/san.cnf"
  openssl x509 -req -in "$GEN/edge.csr" -CA "$GEN/ca.crt" -CAkey "$GEN/ca.key" -CAcreateserial \
    -days 2 -extfile "$GEN/san.cnf" -out "$GEN/edge.crt" 2>/dev/null
  step_scryfall_cert
}

step_scryfall_cert() {
  # The Scryfall mock answers to the real hostname inside the test network (see scryfall_mock.py);
  # the CIMD mock publishes a client metadata document (see cimd_mock.py).
  leaf_cert scryfall api.scryfall.com
  leaf_cert cimd cimd.e2e.test
}

leaf_cert() { # name, dns
  openssl req -new -newkey rsa:2048 -nodes -subj "/CN=$2" \
    -keyout "$GEN/$1.key" -out "$GEN/$1.csr" 2>/dev/null
  printf 'subjectAltName=DNS:%s\n' "$2" > "$GEN/san-$1.cnf"
  openssl x509 -req -in "$GEN/$1.csr" -CA "$GEN/ca.crt" -CAkey "$GEN/ca.key" -CAcreateserial \
    -days 2 -extfile "$GEN/san-$1.cnf" -out "$GEN/$1.crt" 2>/dev/null
}

step_hosts() {
  [ "${E2E_NO_HOSTS:-}" = "1" ] && return
  grep -q 'mtg.e2e.test' /etc/hosts || {
    log "adding mtg.e2e.test and auth.e2e.test to /etc/hosts"
    printf '127.0.0.1 mtg.e2e.test auth.e2e.test\n' | sudo tee -a /etc/hosts >/dev/null 2>&1 \
      || printf '127.0.0.1 mtg.e2e.test auth.e2e.test\n' >> /etc/hosts
  }
}

step_build() {
  [ "${E2E_SKIP_BUILD:-}" = "1" ] && return
  log "building $MTG_IMAGE:$MTG_TAG and $MF_IMAGE:$MF_TAG from the repository Dockerfiles"
  docker build ${E2E_DOCKER_BUILD_ARGS:-} -t "$MTG_IMAGE:$MTG_TAG" "$REPO"
  docker build ${E2E_DOCKER_BUILD_ARGS:-} -f "$REPO/docker/mystic-forge/Dockerfile" -t "$MF_IMAGE:$MF_TAG" "$REPO"
}

step_trust_image() {
  # The gateway talks to Authentik over HTTPS through the edge, whose certificate
  # is signed by the test CA. Production uses public CAs, so the image has no hook
  # for a private CA; this test-only layer adds the CA without touching the code.
  # It goes into the system store (SSL_CERT_FILE, used by clients that read the
  # environment) and into certifi's bundle (used by httpx clients created with
  # trust_env=False, such as the client-metadata-document fetcher).
  log "adding the test CA to a test-only copy of the gateway image ($MTG_IMAGE:$MTG_TAG-trusted)"
  docker build ${E2E_DOCKER_BUILD_ARGS:-} -t "$MTG_IMAGE:$MTG_TAG-trusted" -f - "$GEN" <<DOCKER
FROM $MTG_IMAGE:$MTG_TAG
COPY ca.crt /usr/local/share/ca-certificates/e2e-ca.crt
RUN update-ca-certificates >/dev/null \
 && cat /usr/local/share/ca-certificates/e2e-ca.crt >> "\$(python -c 'import certifi,sys; sys.stdout.write(certifi.where())')"
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
DOCKER
}

step_edge_up() {
  mkdir -p "$GEN"
  [ -f "$GEN/ak.env" ] || {
    printf 'E2E_AK_ADMIN_PASSWORD=%s\nE2E_AK_TOKEN=%s\n' \
      "$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')" \
      "$(python3 -c 'import secrets; print(secrets.token_urlsafe(40))')" > "$GEN/ak.env"
  }
  set -a; . "$GEN/ak.env"; set +a
  log "deploying Authentik, Postgres, Redis and the nginx edge (stack e2eedge)"
  (cd "$HERE/stacks" && docker stack deploy --resolve-image never -c edge-stack.yml e2eedge)
}

step_authentik() {
  set -a; . "$GEN/ak.env"; set +a
  log "DEPLOY.md 3: configuring Authentik provider, application, groups and test users"
  python3 "$HERE/authentik_setup.py" "$AUTH_URL" "$E2E_AK_TOKEN" "$MTG_PUBLIC_URL" "$GEN/authentik.json" "$GEN/ca.crt"
}

step_secrets() {
  # DEPLOY.md step 4, verbatim apart from reading the Authentik secret from the setup output.
  log "DEPLOY.md 4: creating Docker secrets"
  docker secret inspect mtg_fernet_key >/dev/null 2>&1 || \
    python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())' \
      | tee "$GEN/fernet.key" | docker secret create mtg_fernet_key - >/dev/null
  docker secret inspect mtg_session_secret >/dev/null 2>&1 || \
    python3 -c 'import secrets; print(secrets.token_urlsafe(48))' | docker secret create mtg_session_secret - >/dev/null
  docker secret inspect mtg_oidc_client_secret >/dev/null 2>&1 || \
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["client_secret"])' "$GEN/authentik.json" \
      | docker secret create mtg_oidc_client_secret - >/dev/null
  docker secret ls --format '{{.Name}}' | grep -c '^mtg_' | grep -qx 3
}

step_stack_up() {
  log "DEPLOY.md 2: storage directories on the pinned node"
  mkdir -p "$GEN/data" "$GEN/backups"
  chown -R 1000:1000 "$GEN/data" "$GEN/backups" 2>/dev/null || true
  log "DEPLOY.md 6: deploying deploy/portainer-stack.yml as stack mtg"
  export MTG_OIDC_ISSUER MTG_OIDC_CLIENT_ID MTG_REQUIRED_GROUP
  MTG_OIDC_ISSUER="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["issuer"])' "$GEN/authentik.json")"
  MTG_OIDC_CLIENT_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["client_id"])' "$GEN/authentik.json")"
  MTG_REQUIRED_GROUP="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["required_group"])' "$GEN/authentik.json")"
  export MTG_DATA_DIR="$GEN/data" MTG_BACKUP_DIR="$GEN/backups" MTG_NODE="$NODE" MF_NODE="$NODE"
  export PUID=1000 PGID=1000 TIMEZONE=UTC MTG_LOG_LEVEL=DEBUG
  # Deck tools talk to the local mock, never to archidekt.com (deck writes stay off by default,
  # as in stack.env.example; test_05 switches them on and off again with docker service update).
  export MTG_ARCHIDEKT_BASE="${MTG_ARCHIDEKT_BASE:-http://archidektmock:8000/api}"
  export MTG_TAG="$MTG_TAG-trusted"
  docker stack config -c "$REPO/deploy/portainer-stack.yml" > "$GEN/rendered-stack.yml"
  docker stack deploy --resolve-image never -c "$REPO/deploy/portainer-stack.yml" mtg
  if [ "${E2E_SWARM_DNSRR:-}" = "1" ]; then
    # Sandboxes without IPVS cannot route Swarm virtual IPs; production Swarms can, so the
    # stack file keeps the default and only the test run switches the services to DNS round robin.
    log "E2E_SWARM_DNSRR=1: switching the stack's services to endpoint-mode dnsrr"
    docker service update --quiet --endpoint-mode dnsrr mtg_mtg-assistant-mysticforge >/dev/null
    docker service update --quiet --endpoint-mode dnsrr mtg_mtg-assistant-gateway >/dev/null
  fi
}

wait_http() { # url, expected code, seconds
  local url="$1" want="$2" secs="${3:-180}" code=""
  for _ in $(seq 1 "$secs"); do
    code="$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' --cacert "$GEN/ca.crt" "$url" || true)"
    [ "$code" = "$want" ] && return 0
    sleep 2
  done
  echo "timeout waiting for $url to answer $want (last: $code)"; return 1
}

wait_replicas() { # service name
  for _ in $(seq 1 60); do
    [ "$(docker service ls --filter "name=$1" --format '{{.Replicas}}')" = "1/1" ] && return 0
    sleep 2
  done
  echo "timeout waiting for service $1"; docker service ps --no-trunc "$1"; return 1
}

step_verify() {
  # DEPLOY.md step 8, literally.
  log "DEPLOY.md 8: verification curls"
  wait_http "$MTG_PUBLIC_URL/healthz" 200 240
  curl -s --noproxy '*' --cacert "$GEN/ca.crt" "$MTG_PUBLIC_URL/healthz"; echo
  curl -s --noproxy '*' --cacert "$GEN/ca.crt" "$MTG_PUBLIC_URL/.well-known/oauth-authorization-server" | head -c 300; echo
  curl -s --noproxy '*' --cacert "$GEN/ca.crt" -o /dev/null -w '%{http_code}\n' "$MTG_PUBLIC_URL/mcp" | grep -qx 401
  echo "verification curls passed"
}

cmd_up() {
  ensure_swarm; step_network; step_certs; step_hosts; step_build; step_trust_image
  step_edge_up
  log "waiting for Authentik"
  wait_http "$AUTH_URL/-/health/ready/" 200 400
  wait_replicas e2eedge_archidektmock
  wait_replicas e2eedge_scryfallmock
  wait_replicas e2eedge_cimdmock
  step_authentik; step_secrets; step_stack_up; step_verify
}

cmd_test() {
  export E2E_GEN_DIR="$GEN" E2E_PUBLIC_URL="$MTG_PUBLIC_URL" E2E_AUTH_URL="$AUTH_URL"
  (cd "$REPO" && python3 -m pytest -q -o addopts="" "$HERE" -p no:cacheprovider "$@")
}

cmd_down() {
  docker stack rm mtg e2eedge 2>/dev/null || true
  sleep 5
  for s in mtg_fernet_key mtg_session_secret mtg_oidc_client_secret; do docker secret rm "$s" 2>/dev/null || true; done
  for _ in $(seq 1 20); do docker network rm "$MTG_NETWORK_NAME" >/dev/null 2>&1 && break; sleep 3; done
  # The gateway writes data/ and backups/ as uid 1000; on a CI runner that is not us, so
  # fall back to removing them from a root container when a plain rm is refused.
  if ! rm -rf "$GEN" 2>/dev/null; then
    docker run --rm --user 0 --entrypoint rm -v "$GEN:/g" "$MTG_IMAGE:$MTG_TAG" -rf /g/data /g/backups
    rm -rf "$GEN"
  fi
}

case "${1:-all}" in
  up) cmd_up ;;
  test) shift; cmd_test "$@" ;;
  down) cmd_down ;;
  all) cmd_up; cmd_test; cmd_down ;;
  *) echo "usage: $0 up|test|down|all"; exit 2 ;;
esac
