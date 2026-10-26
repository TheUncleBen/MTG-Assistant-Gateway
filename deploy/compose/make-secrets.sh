#!/bin/sh
# Creates the five secret files docker-compose.yml expects, in ./secrets/.
# Existing files are left alone, so it's safe to re-run.
#
#   ./make-secrets.sh            # as the PUID user, or with sudo
#
# The values never touch your shell history or the screen, except the Fernet
# key, which is printed once so you can keep a copy (lose it and everyone has
# to relink Archidekt).
set -eu
umask 077

# A KEY=value from .env, without quotes or a Windows line ending.
from_env_file() {
  [ -f .env ] || return 0
  sed -n "s/^$1=//p" .env | tail -n 1 | tr -d "\"'\r"
}

dir=./secrets
mkdir -p "$dir"

if [ ! -s "$dir/mtg_fernet_key.txt" ]; then
  # A Fernet key: URL-safe base64 of 32 random bytes.
  openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n' > "$dir/mtg_fernet_key.txt.tmp"
  mv "$dir/mtg_fernet_key.txt.tmp" "$dir/mtg_fernet_key.txt"
  echo "mtg_fernet_key (save this in your password manager):"
  cat "$dir/mtg_fernet_key.txt"; echo
fi

if [ ! -s "$dir/mtg_session_secret.txt" ]; then
  openssl rand -base64 48 | tr -d '\n' > "$dir/mtg_session_secret.txt.tmp"
  mv "$dir/mtg_session_secret.txt.tmp" "$dir/mtg_session_secret.txt"
  echo "mtg_session_secret created."
fi

if [ ! -s "$dir/mtg_oidc_client_secret.txt" ]; then
  # Always turn echo back on, even after Ctrl-C or an empty input.
  trap 'stty echo 2>/dev/null || true' EXIT INT TERM
  printf 'Paste the OIDC client secret from your identity provider, then press Enter: '
  stty -echo 2>/dev/null || true
  secret=""
  read -r secret || true
  stty echo 2>/dev/null || true
  echo
  if [ -z "$secret" ]; then
    echo "No client secret entered; nothing written. Run the script again when you have it." >&2
    exit 1
  fi
  printf %s "$secret" > "$dir/mtg_oidc_client_secret.txt.tmp"
  mv "$dir/mtg_oidc_client_secret.txt.tmp" "$dir/mtg_oidc_client_secret.txt"
  unset secret
  echo "mtg_oidc_client_secret created."
fi

if [ ! -s "$dir/mtg_ntfy_token.txt" ]; then
  # An ntfy access token: tk_ and 29 lowercase letters and digits.
  printf 'tk_%s' "$(LC_ALL=C tr -dc a-z0-9 </dev/urandom | head -c 29)" > "$dir/mtg_ntfy_token.txt.tmp"
  mv "$dir/mtg_ntfy_token.txt.tmp" "$dir/mtg_ntfy_token.txt"
  echo "mtg_ntfy_token created."
fi

if [ ! -s "$dir/mtg_authentik_api_token.txt" ]; then
  # The removed-member clean-up's Authentik API token: off until you put a token here
  # (docs/IDP-AUTHENTIK.md, "Optional: removed-member clean-up").
  printf off > "$dir/mtg_authentik_api_token.txt.tmp"
  mv "$dir/mtg_authentik_api_token.txt.tmp" "$dir/mtg_authentik_api_token.txt"
  echo "mtg_authentik_api_token created (off: the removed-member clean-up stays off)."
fi

# Compose mounts these files as they are, so the container's user (PUID) must
# be able to read them. Run as root (sudo), the script hands them to PUID:PGID
# from the environment or .env (default 1000:1000); otherwise they stay yours,
# which is right when you are the PUID user.
chmod 600 "$dir"/*.txt
if [ "$(id -u)" = "0" ]; then
  puid="${PUID:-$(from_env_file PUID)}"
  pgid="${PGID:-$(from_env_file PGID)}"
  chown "${puid:-1000}:${pgid:-1000}" "$dir" "$dir"/*.txt
fi
echo "Done. Files in $dir:"
ls -l "$dir"
