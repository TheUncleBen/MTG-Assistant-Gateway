#!/usr/bin/env bash
# CI smoke test of a Forge image (.github/workflows/forge-image.yml): drops to PUID/PGID, answers
# /health with Forge's card count, resolves card names, and plays one two-player game to the end.
#   scripts/ci_smoke_forge.sh <local image tag>
set -eux
docker run -d --name forge -p 18100:8000 -e PUID=1234 -e PGID=1234 -e FORGE_XMX=1g "$1"
for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:18100/health; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:18100/health | python3 -c 'import json,sys; h=json.load(sys.stdin); print(h); sys.exit(h["cards"] < 30000)'
docker exec forge grep '^Uid:' /proc/1/status | grep -qw 1234
echo "Idle:"; docker stats --no-stream --format '{{.MemUsage}}' forge
curl -fsS -X POST http://127.0.0.1:18100/check -H 'Content-Type: application/json' \
  -d '{"names":["liesa, forgotten archangel","Fire // Ice","Not A Real Card"]}' \
  | python3 -c 'import json,sys; r=json.load(sys.stdin)["resolved"]; print(r); sys.exit(not (r["liesa, forgotten archangel"]=="Liesa, Forgotten Archangel" and r["Fire // Ice"] and r["Not A Real Card"] is None))'
red='[[24,"Mountain"],[4,"Raging Goblin"],[4,"Goblin Piker"],[4,"Hill Giant"],[4,"Gray Ogre"],[4,"Lightning Bolt"],[4,"Shock"],[4,"Goblin Raider"],[4,"Bloodrock Cyclops"],[4,"Fire Elemental"]]'
green='[[24,"Forest"],[4,"Grizzly Bears"],[4,"Llanowar Elves"],[4,"Giant Growth"],[4,"Craw Wurm"],[4,"Runeclaw Bear"],[4,"Giant Spider"],[4,"Elvish Warrior"],[4,"Trained Armodon"],[4,"Spined Wurm"]]'
job=$(curl -fsS -X POST http://127.0.0.1:18100/jobs -H 'Content-Type: application/json' \
  -d "{\"format\":\"Constructed\",\"games\":2,\"decks\":[{\"main\":$red},{\"main\":$green}]}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
peak=0
for i in $(seq 1 120); do
  view=$(curl -fsS "http://127.0.0.1:18100/jobs/$job")
  state=$(echo "$view" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')
  mem=$(docker stats --no-stream --format '{{.MemUsage}}' forge); echo "$state $mem"
  case "$state" in queued|running) sleep 5 ;; *) break ;; esac
done
echo "$view" | python3 -m json.tool
echo "$view" | python3 -c 'import json,sys; v=json.load(sys.stdin); sys.exit(not (v["state"]=="done" and v["games_done"]==2 and all(r["winner"] in (1,2) or r["draw"] for r in v["results"])))'
docker logs forge | tail -20
docker rm -f forge
