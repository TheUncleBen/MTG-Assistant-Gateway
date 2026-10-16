#!/usr/bin/env bash
# Measures Forge games played one, two and three at once (Q136 item 5), inside the deployment's own limits
# (FORGE_MEMORY_LIMIT 1536M, FORGE_CPUS 2): run time, peak memory, whether the kernel killed a process for
# memory, and whether the same seed gave the same results each way. A run plays as many groups at once as
# it has groups (forge_service.py: groups of at most five), so three at once needs at least 11 games; the
# default 15 is three full groups.
# Each run gets a fresh container, so a memory kill, a run cut off at the time limit or memory left
# behind never carries into the next run. The container gets no swap (--memory-swap equal to --memory):
# a run that needs more than 1536 MiB is killed instead of swapping, so the peak cannot hide memory that
# went to swap. A deployment whose host has swap may get further before a kill; that is not measured.
# Peak memory and kills are read from the container's own cgroup (memory.peak, memory.events oom_kill),
# with polled `docker stats` as a fallback for the peak.
# Numbers only: it never fails the job (.github/workflows/forge-image.yml runs it with continue-on-error).
#   scripts/ci_measure_forge.sh <local image tag> [games]
set -u
image="$1"
games="${2:-15}"
if [ "$games" -lt 11 ]; then echo "need at least 11 games for three groups at once" >&2; exit 0; fi
# the longest one run may take before it is stopped and reported as unfinished (seconds; 50 minutes)
run_limit="${MEASURE_RUN_LIMIT:-3000}"
summary="${GITHUB_STEP_SUMMARY:-/dev/stdout}"
url=http://127.0.0.1:18101

start() {
  docker rm -f forge-measure >/dev/null 2>&1 || true
  docker run -d --name forge-measure -p 18101:8000 --memory 1536m --memory-swap 1536m --cpus 2 \
    -e FORGE_XMX=1g -e FORGE_MAX_PARALLEL=3 "$image" >/dev/null
  for i in $(seq 1 60); do curl -fsS "$url/health" >/dev/null 2>&1 && return 0; sleep 1; done
  return 1
}

# cg <file>: a value from the container's cgroup v2 files, or "unknown"
cg() { docker exec forge-measure cat "/sys/fs/cgroup/$1" 2>/dev/null || echo unknown; }

echo "Runner: $(nproc) CPUs, $(free -m | awk '/Mem:/ {print $2}') MiB RAM, $(free -m | awk '/Swap:/ {print $2}') MiB swap; container limit 1536 MiB, no swap, 2 CPUs"
start || { echo "Forge service did not start"; exit 0; }
precons=$(curl -fsS "$url/precons" | python3 -c 'import json,sys; p=json.load(sys.stdin)["precons"]; print(json.dumps([{"precon": d["name"]} for d in p[:4]]))')

# measure <parallel>: one job of $games four-player Commander games, seed 42, in a fresh container;
# prints a summary line and leaves the job's results in results-<parallel>.json
measure() {
  start || { echo "asked $1 at once: the Forge service did not start"; return; }
  body="{\"format\":\"Commander\",\"games\":$games,\"seed\":42,\"parallel\":$1,\"decks\":$precons}"
  job=$(curl -fsS -X POST "$url/jobs" -H 'Content-Type: application/json' -d "$body" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])') || { echo "asked $1 at once: not started"; return; }
  polled=0
  view='{}'
  cut=no
  end=$((SECONDS + run_limit))
  while :; do
    if [ "$SECONDS" -ge "$end" ]; then
      cut=yes
      curl -fsS -X DELETE "$url/jobs/$job" >/dev/null 2>&1 || true
      break
    fi
    mem=$(docker stats --no-stream --format '{{.MemUsage}}' forge-measure | awk '{print $1}')
    mib=$(python3 -c 'import re,sys; v=sys.argv[1]; n=float(re.match(r"[\d.]+",v).group()); print(int(n*1024 if "GiB" in v else n/1024 if "KiB" in v else n))' "$mem" 2>/dev/null || echo 0)
    [ "$mib" -gt "$polled" ] && polled=$mib
    view=$(curl -fsS "$url/jobs/$job") || break
    state=$(echo "$view" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')
    case "$state" in queued|running) sleep 2 ;; *) break ;; esac
  done
  peak=$(cg memory.peak)
  kills=$(cg memory.events | awk '$1 == "oom_kill" {print $2}')
  echo "$view" > "results-$1.json"
  echo "$view" | python3 -c '
import json, sys
v = json.loads(sys.stdin.read() or "{}")
games = v.get("results") or []
peak = sys.argv[3]
peak = "%d MiB (cgroup)" % (int(peak) // 2**20) if peak.isdigit() else "%s MiB (polled; cgroup peak unknown)" % sys.argv[2]
print("asked %s at once (played %s at once): state %s, %d of %s games, run %.0f s, peak %s, killed for memory %s time(s), cut off at the time limit %s, game ms %s" % (
    sys.argv[1], v.get("parallel"), v.get("state"), len(games), v.get("games_requested"), v.get("run_s") or 0,
    peak, sys.argv[4] or "unknown", sys.argv[5], [r.get("ms") for r in games]))' "$1" "$polled" "$peak" "$kills" "$cut"
  docker logs forge-measure 2>&1 | tail -3
}

{
  echo "### Forge games at once (${games} four-player games, seed 42, 1536 MiB with no swap / 2 CPUs, a fresh container per run)"
  echo '```'
  measure 1
  measure 2
  measure 3
  python3 - <<'PY'
import json, os
key = lambda rs: [(r["game"], r.get("winner"), r.get("draw"), r.get("turn")) for r in rs]
runs = {p: json.load(open(f"results-{p}.json")).get("results") or [] for p in (1, 2, 3) if os.path.exists(f"results-{p}.json")}
for p in (2, 3):
    if 1 in runs and p in runs:
        same = key(runs[1]) == key(runs[p]) and runs[1]
        print(f"same seed, same results at 1 and at {p} at once:", "yes" if same else "no", key(runs[1]), key(runs[p]))
PY
  echo '```'
} | tee -a "$summary"
docker rm -f forge-measure >/dev/null 2>&1
exit 0
