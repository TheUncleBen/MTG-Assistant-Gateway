"""Forge feasibility spike: card coverage, memory, speed and repeatability on arm64.

Throwaway measurement for the simulator decision (R-134); not part of the gateway.
Run from the workflow in .github/workflows/spike-forge.yml. Writes out/summary.json,
out/summary.md and the raw logs to out/.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

FORGE = Path(sys.argv[1]).resolve()        # extracted Forge release
HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[2]).resolve()
DECKS = OUT / "decks"
UA = {"User-Agent": "MTGAssistantGateway-spike/1.0", "Accept": "application/json"}
GAMES = int(os.environ.get("SPIKE_GAMES", "10"))
RUN_TIMEOUT = int(os.environ.get("SPIKE_RUN_TIMEOUT", "2400"))


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def forge_jar() -> Path:
    jars = sorted(FORGE.glob("*jar-with-dependencies.jar"))
    if not jars:
        raise SystemExit(f"no Forge jar in {FORGE}: {sorted(p.name for p in FORGE.iterdir())}")
    return jars[-1]


# ---------- card coverage ----------

def forge_card_names() -> set[str]:
    folder = FORGE / "res" / "cardsfolder"
    names: set[str] = set()

    def scan(text: str) -> None:
        # The first Name: is the card (front face); faces after ALTERNATE are other faces.
        m = re.search(r"^Name:(.+)$", text, re.MULTILINE)
        if m:
            names.add(m.group(1).strip())

    zpath = folder / "cardsfolder.zip"
    if zpath.exists():
        with zipfile.ZipFile(zpath) as z:
            for info in z.infolist():
                if info.filename.endswith(".txt"):
                    scan(z.read(info).decode("utf-8", "replace"))
    for p in folder.rglob("*.txt"):
        scan(p.read_text("utf-8", "replace"))
    return names


def scryfall_commander_pool() -> list[dict]:
    bulk = json.loads(get("https://api.scryfall.com/bulk-data/oracle-cards"))
    url = bulk.get("download_uri") or bulk["jsonl_download_uri"]
    raw = get(url)
    if url.endswith(".gz"):
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8")
    cards = [json.loads(line) for line in text.splitlines() if line.strip()] if url.endswith(
        (".jsonl", ".jsonl.gz")) else json.loads(text)
    return [c for c in cards if c.get("legalities", {}).get("commander") in ("legal", "restricted")]


def coverage() -> dict:
    forge = forge_card_names()
    forge_low = {n.lower() for n in forge}
    pool = scryfall_commander_pool()
    missing = []
    for c in pool:
        full = c["name"]
        front = full.split(" // ")[0]
        if full.lower() not in forge_low and front.lower() not in forge_low:
            missing.append({"name": full, "set": c.get("set"), "type_line": c.get("type_line"),
                            "released_at": c.get("released_at")})
    (OUT / "forge-missing-commander-cards.json").write_text(json.dumps(missing, indent=1))
    n = len(pool)
    return {"forge_scripts": len(forge), "commander_pool": n, "missing": len(missing),
            "covered_pct": round(100 * (n - len(missing)) / n, 2),
            "missing_sample": [m["name"] for m in missing[:60]]}


# ---------- decks ----------

def front(name: str) -> str:
    return name.split(" // ")[0]


def precon_decks(count: int = 3) -> list[str]:
    """Write the newest `count` Commander precons from MTGJSON as Forge .dck files."""
    listing = json.loads(get("https://mtgjson.com/api/v5/DeckList.json"))["data"]
    cmdr = [d for d in listing if d.get("type") == "Commander Deck" and d.get("releaseDate")]
    cmdr.sort(key=lambda d: d["releaseDate"], reverse=True)
    written = []
    for meta in cmdr:
        if len(written) >= count:
            break
        deck = json.loads(get(f"https://mtgjson.com/api/v5/decks/{meta['fileName']}.json"))["data"]
        commanders = deck.get("commander") or []
        main = deck.get("mainBoard") or []
        if not commanders or sum(c.get("count", 1) for c in commanders + main) != 100:
            continue
        lines = ["[metadata]", f"Name={meta['name']}", "[Commander]"]
        lines += [f"{c.get('count', 1)} {front(c['name'])}" for c in commanders]
        lines += ["[Main]"] + [f"{c.get('count', 1)} {front(c['name'])}" for c in main]
        fname = re.sub(r"[^A-Za-z0-9]+", "_", meta["name"]).strip("_") + ".dck"
        (DECKS / fname).write_text("\n".join(lines) + "\n")
        written.append(fname)
    return written


# ---------- simulation ----------

def time_v(cmd: list[str], log: Path) -> dict:
    t0 = time.monotonic()
    with log.open("w") as fh:
        try:
            p = subprocess.run(["/usr/bin/time", "-v", *cmd], cwd=FORGE, stdout=fh,
                               stderr=subprocess.STDOUT, timeout=RUN_TIMEOUT)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
    wall = time.monotonic() - t0
    text = log.read_text("utf-8", "replace")
    rss = re.search(r"Maximum resident set size \(kbytes\): (\d+)", text)
    return {"rc": rc, "wall_s": round(wall, 1),
            "max_rss_mb": round(int(rss.group(1)) / 1024) if rss else None}


def game_results(text: str) -> list[str]:
    """Forge prints one line per finished game; keep the lines naming a winner or a draw."""
    keep = []
    for line in text.splitlines():
        low = line.lower()
        if re.search(r"game( outcome)?\s*\d*.*(won|wins|winner|draw|ended)", low) or "has won" in low:
            keep.append(line.strip())
    return keep


def sim(label: str, xmx: str, decks: list[str], headless: bool, games: int) -> dict:
    cmd = ["java", f"-Xmx{xmx}", "-jar", str(forge_jar()), "sim", "-D", str(DECKS),
           "-d", *decks, "-f", "Commander", "-n", str(games), "-q"]
    if headless:
        cmd.insert(1, "-Djava.awt.headless=true")
    else:
        cmd = ["xvfb-run", "-a", *cmd]
    log = OUT / f"sim-{label}.log"
    r = time_v(cmd, log)
    text = log.read_text("utf-8", "replace")
    results = game_results(text)
    r.update({"label": label, "xmx": xmx, "headless": headless, "games_requested": games,
              "result_lines": results, "games_reported": len(results),
              "unknown_card_lines": [ln.strip() for ln in text.splitlines()
                                     if re.search(r"(could not|cannot|can't|unable to) (find|load)|unsupported|not supported|unknown card", ln, re.I)][:40]})
    if r["games_reported"]:
        r["s_per_game"] = round(r["wall_s"] / r["games_reported"], 1)
    return r


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    DECKS.mkdir(parents=True, exist_ok=True)
    summary: dict = {"forge_dir": FORGE.name, "jar": forge_jar().name,
                     "arch": os.uname().machine, "cpus": os.cpu_count()}

    usage = subprocess.run(["java", "-Djava.awt.headless=true", "-jar", str(forge_jar()), "sim"],
                           cwd=FORGE, capture_output=True, text=True, timeout=600)
    (OUT / "sim-usage.txt").write_text(usage.stdout + usage.stderr)
    summary["sim_usage_mentions_seed"] = bool(re.search(r"seed", usage.stdout + usage.stderr, re.I))

    try:
        summary["coverage"] = coverage()
    except Exception as exc:  # keep going: the sim numbers matter as much
        summary["coverage"] = {"error": repr(exc)}

    (DECKS / "liesa.dck").write_text((HERE / "liesa.dck").read_text())
    opponents = precon_decks(3)
    summary["opponents"] = opponents
    decks = ["liesa.dck", *opponents]

    runs = []
    # Smoke: does sim mode run without a display? Fall back to a virtual display if not.
    smoke = sim("smoke-headless", "2g", decks, headless=True, games=1)
    headless = smoke["games_reported"] > 0
    runs.append(smoke)
    if not headless:
        smoke2 = sim("smoke-xvfb", "2g", decks, headless=False, games=1)
        runs.append(smoke2)
    for xmx in ("2g", "1g", "768m"):
        runs.append(sim(f"run-{xmx}", xmx, decks, headless=headless, games=GAMES))
    runs.append(sim("repeat-2g", "2g", decks, headless=headless, games=GAMES))
    summary["needs_virtual_display"] = not headless
    summary["runs"] = runs
    a = next(r for r in runs if r["label"] == "run-2g")["result_lines"]
    b = next(r for r in runs if r["label"] == "repeat-2g")["result_lines"]
    summary["repeat_identical"] = bool(a) and a == b

    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))
    md = ["# Forge spike", "", f"arch {summary['arch']}, {summary['cpus']} CPUs, {summary['jar']}", "",
          f"Coverage: {json.dumps(summary['coverage'])[:600]}", "",
          f"Seed flag in sim usage: {summary['sim_usage_mentions_seed']}; virtual display needed: {not headless}; "
          f"repeat identical: {summary['repeat_identical']}", "",
          "| run | Xmx | rc | wall s | games | s/game | max RSS MB |", "|---|---|---|---|---|---|---|"]
    for r in runs:
        md.append(f"| {r['label']} | {r['xmx']} | {r['rc']} | {r['wall_s']} | {r['games_reported']} | "
                  f"{r.get('s_per_game', '')} | {r['max_rss_mb']} |")
    (OUT / "summary.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
