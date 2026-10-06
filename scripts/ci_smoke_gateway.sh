#!/usr/bin/env bash
# CI smoke test of a gateway image (.github/workflows/ci.yml): starts as root, drops to PUID/PGID,
# answers /healthz, refuses /mcp without a token, and serves the plugin pages without sign-in.
#   scripts/ci_smoke_gateway.sh <local image tag>
set -eux
image="$1"
dir="${RUNNER_TEMP:-$(mktemp -d)}/secrets"
mkdir -p "$dir"
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > "$dir/session"
python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())' > "$dir/fernet"
echo dummy-client-secret > "$dir/oidc"
chmod 0444 "$dir"/*
docker run -d --name gw -p 18080:8080 \
  -e PUID=1234 -e PGID=1234 -e TZ=UTC \
  -e MTG_PUBLIC_URL=https://mtg.example.test \
  -e MTG_OIDC_ISSUER=https://auth.example.test/application/o/mtg/ \
  -e MTG_OIDC_CLIENT_ID=ci \
  -e MTG_OIDC_CLIENT_SECRET_FILE=/run/secrets/oidc \
  -e MTG_FERNET_KEY_FILE=/run/secrets/fernet \
  -e MTG_SESSION_SECRET_FILE=/run/secrets/session \
  -e MTG_REQUIRED_GROUP=ci-users \
  -v "$dir:/run/secrets:ro" \
  "$image"
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:18080/healthz; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:18080/healthz | grep '"ok"'
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/mcp | grep -q 401
curl -fsS -H 'Host: mtg.example.test' http://127.0.0.1:18080/.well-known/oauth-authorization-server | grep -q authorize
# PID 1 must be the gateway running as the requested PUID, not root.
docker exec gw grep '^Uid:' /proc/1/status | grep -qw 1234
docker exec gw mtg-gateway backup
docker exec gw ls /backups
# The assistant plugin and its skill ship in the image and are readable by the gateway user.
docker exec --user 1234:1234 gw python -c "from mtg_gateway.skill_page import skill_dir, build_zip; d = skill_dir(); assert d and str(d).startswith('/usr/share/'), d; assert len(build_zip(d)) > 0"
# The install page and marketplace need no sign-in.
curl -fsS -H 'Host: mtg.example.test' http://127.0.0.1:18080/plugin/marketplace.json | grep -q '"sha256"'
curl -fsS -H 'Host: mtg.example.test' http://127.0.0.1:18080/install.md | grep -q 'claude plugin install'
docker logs gw
docker rm -f gw
