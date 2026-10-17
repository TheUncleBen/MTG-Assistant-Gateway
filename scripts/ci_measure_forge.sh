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
# Three memory peaks are reported, as they measure different things: the programs' own memory (the
# cgroup's memory.stat anon, polled every few seconds), `docker stats` (RAM less inactive file cache,
# polled), and the cgroup's memory.peak (exact, but it includes file cache such as Forge's card data).
# Kills come from the cgroup's memory.events oom_kill. A Java heap error (OutOfMemoryError) is not a
# kernel kill: read the kill count together with the state and the number of games.
# Every poll has a time limit (30 s), so a service that hangs rather than dies still reaches the cut-off.
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
  for i in $(seq 1 60); do curl -fsS --max-time 5 "$url/health" >/dev/null 2>&1 && return 0; sleep 1; done
  return 1
}

# cg <file>: a value from the container's cgroup v2 files, or "unknown"
cg() { timeout 30 docker exec forge-measure cat "/sys/fs/cgroup/$1" 2>/dev/null || echo unknown; }

start || { echo "Forge service did not start"; exit 0; }
precons=$(curl -fsS --max-time 30 "$url/precons" | python3 -c 'import json,sys; p=json.load(sys.stdin)["precons"]; print(json.dumps([{"precon": d["name"]} for d in p[:4]]))')

# mib <docker stats figure>: the figure in MiB
mib() { python3 -c 'import re,sys; v=sys.argv[1]; n=float(re.match(r"[\d.]+",v).group()); print(int(n*1024 if "GiB" in v else n/1024 if "KiB" in v else n))' "$1" 2>/dev/null || echo 0; }

# measure <parallel>: one job of $games four-player Commander games, seed 42, in a fresh container;
# prints a summary line and leaves the job's results in results-<parallel>.json
measure() {
  start || { echo "asked $1 at once: the Forge service did not start"; return; }
  body="{\"format\":\"Commander\",\"games\":$games,\"seed\":42,\"parallel\":$1,\"decks\":$precons}"
  job=$(curl -fsS --max-time 30 -X POST "$url/jobs" -H 'Content-Type: application/json' -d "$body" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])') || { echo "asked $1 at once: not started"; return; }
  polled=0  # docker stats (RAM less inactive file cache), polled
  anon=0    # the programs' own memory (memory.stat anon, no file cache), polled
  view='{}'
  cut=no
  lost=no
  end=$((SECONDS + run_limit))
  while :; do
    if [ "$SECONDS" -ge "$end" ]; then
      cut=yes
      curl -fsS --max-time 30 -X DELETE "$url/jobs/$job" >/dev/null 2>&1 || true
      # the state after the cancel: the service marks the job cancelled once its games have stopped
      for i in $(seq 1 15); do
        sleep 2
        view=$(curl -fsS --max-time 30 "$url/jobs/$job") || { lost=yes; break; }
        state=$(echo "$view" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')
        [ "$state" = running ] || break
      done
      break
    fi
    m=$(mib "$(timeout 30 docker stats --no-stream --format '{{.MemUsage}}' forge-measure | awk '{print $1}')")
    [ "$m" -gt "$polled" ] && polled=$m
    a=$(cg memory.stat | awk '$1 == "anon" {print int($2 / 1048576)}')
    [ -n "$a" ] && [ "$a" -gt "$anon" ] 2>/dev/null && anon=$a
    next=$(curl -fsS --max-time 30 "$url/jobs/$job") || { lost=yes; break; }
    view=$next
    state=$(echo "$view" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')
    case "$state" in queued|running) sleep 2 ;; *) break ;; esac
  done
  peak=$(cg memory.peak)
  kills=$(cg memory.events | awk '$1 == "oom_kill" {print $2}')
  gone=""
  if [ "$lost" = yes ]; then
    gone=$(docker inspect -f 'running {{.State.Running}}, OOM-killed {{.State.OOMKilled}}, exit code {{.State.ExitCode}}' forge-measure 2>/dev/null || echo "container gone")
  fi
  echo "$view" > "results-$1.json"
  echo "$view" | python3 -c '
import json, sys
p, polled, anon, peak, kills, cut, gone, asked = sys.argv[1:9]
v = json.loads(sys.stdin.read() or "{}")
games = v.get("results") or []
state = "the service stopped answering (%s); last state seen %s" % (gone, v.get("state") or "none") if gone else "state %s" % (v.get("state") or "none")
mem = "peak: programs %s (anon, polled), docker stats %s (polled)" % (
    "%s MiB" % anon if anon != "0" else "unknown", "%s MiB" % polled if polled != "0" else "unknown")
mem += ", cgroup %d MiB (includes file cache)" % (int(peak) // 2**20) if peak.isdigit() else ", cgroup peak unknown"
print("asked %s at once (played %s at once): %s, %d of %s games, run %.0f s, %s, killed for memory: %s, cut off at the time limit: %s, game ms %s" % (
    p, v.get("parallel") or "?", state, len(games), v.get("games_requested") or asked, v.get("run_s") or 0,
    mem, kills or "unknown", cut, [r.get("ms") for r in games]))' "$1" "$polled" "$anon" "$peak" "$kills" "$cut" "$gone" "$games"
  docker logs forge-measure 2>&1 | tail -3
}

{
  echo "### Forge games at once (${games} four-player games, seed 42, 1536 MiB with no swap / 2 CPUs, a fresh container per run)"
  echo '```'
  echo "Memory peaks: programs = the programs' own memory, without file cache; docker stats = RAM less inactive file cache; cgroup = the exact peak, including file cache such as Forge's card data. Polled figures can miss short spikes."
  echo "Runner: $(nproc) CPUs, $(free -m | awk '/Mem:/ {print $2}') MiB RAM, $(free -m | awk '/Swap:/ {print $2}') MiB swap"
  measure 1
  measure 2
  measure 3
  python3 - <<'PY'
import json, os
key = lambda rs: [(r["game"], r.get("winner"), r.get("draw"), r.get("turn")) for r in rs]
runs = {p: json.load(open(f"results-{p}.json")) for p in (1, 2, 3) if os.path.exists(f"results-{p}.json")}
done = lambda v: v.get("state") == "done" and len(v.get("results") or []) == v.get("games_requested")
for p in (2, 3):
    if 1 not in runs or p not in runs or not (done(runs[1]) and done(runs[p])):
        print(f"same seed at 1 and at {p} at once: not compared (a run did not finish)")
        continue
    same = key(runs[1]["results"]) == key(runs[p]["results"])
    print(f"same seed, same results at 1 and at {p} at once:", "yes" if same else "no",
          key(runs[1]["results"]), key(runs[p]["results"]))
PY
  echo '```'
} | tee -a "$summary"
docker rm -f forge-measure >/dev/null 2>&1
exit 0
