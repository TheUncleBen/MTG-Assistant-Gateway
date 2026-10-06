#!/bin/sh
# Drop to PUID:PGID when started as root (the usual self-hosted stack convention
# of setting PUID/PGID on every service). Docker secrets are mounted world-readable by default, so the
# unprivileged user can still read them.
set -eu
# Database, backups and anything else the gateway writes are readable by its own user only.
umask 077
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
if [ "$(id -u)" = "0" ]; then
  if [ "$PGID" != "$(id -g mtg)" ]; then groupmod -o -g "$PGID" mtg; fi
  if [ "$PUID" != "$(id -u mtg)" ]; then usermod -o -u "$PUID" mtg; fi
  for d in "${MTG_DATA_DIR:-/data}" "${MTG_BACKUP_DIR:-/backups}"; do
    [ -d "$d" ] && chown "$PUID:$PGID" "$d" || true
  done
  exec setpriv --reuid="$PUID" --regid="$PGID" --init-groups --no-new-privs "$@"
fi
exec "$@"
