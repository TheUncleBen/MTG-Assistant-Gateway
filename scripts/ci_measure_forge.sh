#!/usr/bin/env bash
# Measures Forge games played one, two and three at once (Q136 item 5), inside the deployment's own limits
# (FORGE_MEMORY_LIMIT 1536M, FORGE_CPUS 2): run time, peak memory, whether the container was killed
# for memory, and whether the same seed gave the same games each way. Numbers only: it never fails
# the job (.github/workflows/forge-image.yml runs it with continue-on-error).
#   scripts/ci_measure_forge.sh <local image tag> [games]
set -u
image="$1"
games="${2:-6}"
summary="${GITHUB_STEP_SUMMARY:-/dev/stdout}"
docker rm -f forge-measure >/dev/null 2>&1 || true
docker run -d --name forge-measure -p 18101:8000 --memory 1536m --cpus 2 \
  -e FORGE_XMX=1g -e FORGE_MAX_PARALLEL=3 "$image" >/dev/null
for i in $(seq 1 60); do curl -fsS http://127.0.0.1:18101/health >/dev/null 2>&1 && break; sleep 1; done
echo "Runner: $(nproc) CPUs, $(free -m | awk '/Mem:/ {print $2}') MiB; container limit 1536 MiB, 2 CPUs"
precons=$(curl -fsS http://127.0.0.1:18101/precons | python3 -c 'import json,sys; p=json.load(sys.stdin)["precons"]; print(json.dumps([{"precon": d["name"]} for d in p[:4]]))')

# measure <parallel>: one job of $games four-player Commander games, seed 42; prints a summary line
# and leaves the job's results in results-<parallel>.json
measure() {
  body="{\"format\":\"Commander\",\"games\":$games,\"seed\":42,\"parallel\":$1,\"decks\":$precons}"
  job=$(curl -fsS -X POST http://127.0.0.1:18101/jobs -H 'Content-Type: application/json' -d "$body" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])') || { echo "parallel $1: not started"; return; }
  peak=0
  for i in $(seq 1 360); do
    mem=$(docker stats --no-stream --format '{{.MemUsage}}' forge-measure | awk '{print $1}')
    mib=$(python3 -c 'import re,sys; v=sys.argv[1]; n=float(re.match(r"[\d.]+",v).group()); print(int(n*1024 if "GiB" in v else n/1024 if "KiB" in v else n))' "$mem" 2>/dev/null || echo 0)
    [ "$mib" -gt "$peak" ] && peak=$mib
    view=$(curl -fsS "http://127.0.0.1:18101/jobs/$job") || break
    state=$(echo "$view" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')
    case "$state" in queued|running) sleep 2 ;; *) break ;; esac
  done
  echo "$view" > "results-$1.json"
  oom=$(docker inspect -f '{{.State.OOMKilled}}' forge-measure)
  echo "$view" | python3 -c '
import json, sys
v = json.load(sys.stdin)
games = [r for r in v["results"]]
print("parallel %s (used %s): state %s, %d of %d games, run %.0f s, peak %s MiB, OOM-killed %s, game ms %s" % (
    sys.argv[1], v.get("parallel"), v["state"], len(games), v["games_requested"], v.get("run_s") or 0,
    sys.argv[2], sys.argv[3], [r["ms"] for r in games]))' "$1" "$peak" "$oom"
}

{
  echo "### Forge games at once (${games} four-player games, seed 42, 1536 MiB / 2 CPUs)"
  echo '```'
  measure 1
  measure 2
  measure 3
  python3 - <<'PY'
import json, os
key = lambda rs: [(r["game"], r.get("winner"), r.get("draw"), r.get("turn")) for r in rs]
runs = {p: json.load(open(f"results-{p}.json"))["results"] for p in (1, 2, 3) if os.path.exists(f"results-{p}.json")}
for p in (2, 3):
    if 1 in runs and p in runs:
        same = key(runs[1]) == key(runs[p]) and runs[1]
        print(f"same seed, same games at 1 and at {p}:", "yes" if same else "no", key(runs[1]), key(runs[p]))
PY
  echo '```'
} | tee -a "$summary"
docker logs forge-measure 2>&1 | tail -5
docker rm -f forge-measure >/dev/null
exit 0
