#!/bin/sh
# Drop to PUID:PGID when started as root, matching the gateway image.
# /data holds Forge's preferences and logs (it writes them under $HOME); nothing there is kept.
set -eu
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
if [ "$(id -u)" = "0" ]; then
  if [ "$PGID" != "$(id -g forge)" ]; then groupmod -o -g "$PGID" forge; fi
  if [ "$PUID" != "$(id -u forge)" ]; then usermod -o -u "$PUID" forge; fi
  chown "$PUID:$PGID" /data
  exec setpriv --reuid="$PUID" --regid="$PGID" --init-groups --no-new-privs "$@"
fi
exec "$@"
