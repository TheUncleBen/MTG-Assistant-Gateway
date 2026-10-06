#!/usr/bin/env bash
# CI smoke test of a Mystic Forge image (.github/workflows/mystic-forge-image.yml): drops to PUID/PGID,
# answers /health, lists its 46 tools and answers two offline tool calls.
#   scripts/ci_smoke_mysticforge.sh <local image tag>
set -eux
docker run -d --name mf -p 18000:8000 -e PUID=1234 -e PGID=1234 -e TZ=UTC \
  -e MYSTIC_FORGE_PUBLIC_BASE=http://mtg-assistant-mysticforge:8000 "$1"
for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:18000/health; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:18000/health | grep '"status":"ok"'
docker exec mf grep '^Uid:' /proc/1/status | grep -qw 1234
mcp() {
  curl -fsS -X POST http://127.0.0.1:18000/mcp \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' -d "$1" \
    | sed -n 's/^data: //p'
}
mcp '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  | python3 -c 'import json,sys; t=json.load(sys.stdin)["result"]["tools"]; print(len(t)); sys.exit(len(t)!=46)'
mcp '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"goldfish_odds","arguments":{"params":{"deck_size":99,"draws":7,"copies":36,"min_successes":3}}}}' \
  | grep -q '50.11%'
mcp '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"rules_get","arguments":{"params":{"ref":"704.5a"}}}}' \
  | grep -q '0 or less life'
docker stats --no-stream mf
docker logs mf
docker rm -f mf
