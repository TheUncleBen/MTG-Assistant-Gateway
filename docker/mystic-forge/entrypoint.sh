#!/bin/sh
# Drop to PUID:PGID when started as root, matching the gateway image.
# /data holds Mystic Forge's SQLite file and rules cache.
set -eu
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
if [ "$(id -u)" = "0" ]; then
  if [ "$PGID" != "$(id -g mf)" ]; then groupmod -o -g "$PGID" mf; fi
  if [ "$PUID" != "$(id -u mf)" ]; then usermod -o -u "$PUID" mf; fi
  chown "$PUID:$PGID" /data
  exec setpriv --reuid="$PUID" --regid="$PGID" --init-groups --no-new-privs "$@"
fi
exec "$@"
