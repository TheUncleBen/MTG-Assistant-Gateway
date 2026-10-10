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
        # Every face's Name: (front, back, meld result, adventure part).
        for m in re.finditer(r"^Name:(.+)$", text, re.MULTILINE):
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
    return cards


NON_CARDS = {"token", "double_faced_token", "emblem", "art_series", "vanguard", "scheme", "planar"}


def norm(name: str) -> str:
    import unicodedata
    n = unicodedata.normalize("NFKD", name.replace("\ua789", ":")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", n).strip().lower()


def print_names() -> dict:
    """oracle_id -> every name any printing carries (printed, flavor/Universes Within, faces)."""
    bulk = json.loads(get("https://api.scryfall.com/bulk-data/default-cards"))
    raw = gzip.decompress(get(bulk["jsonl_download_uri"])).decode("utf-8")
    names: dict = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        oid = c.get("oracle_id") or (c.get("card_faces") or [{}])[0].get("oracle_id")
        if not oid:
            continue
        bag = names.setdefault(oid, set())
        for k in ("name", "printed_name", "flavor_name"):
            if c.get(k):
                bag.add(c[k])
        for f in c.get("card_faces") or []:
            for k in ("name", "printed_name", "flavor_name"):
                if f.get(k):
                    bag.add(f[k])
    return names


def coverage() -> dict:
    forge = forge_card_names()
    (OUT / "forge-card-names.json").write_text(json.dumps(sorted(forge), indent=0))
    forge_low = {n.lower() for n in forge}
    forge_norm = {norm(n) for n in forge}
    try:
        pnames = print_names()
    except Exception as exc:
        print("print names unavailable:", repr(exc))
        pnames = {}
    allc = [c for c in scryfall_commander_pool() if c.get("layout") not in NON_CARDS]
    def playable(c: dict) -> bool:
        return (c.get("set_type") not in ("funny", "memorabilia", "token", "minigame")
                and "paper" in (c.get("games") or []))
    # A missing card whose exact rules text and type line match a card Forge has can be played as
    # that card (Universes Within renames, reprints under another name).
    by_text: dict = {}
    for c in allc:
        key = ((c.get("oracle_text") or "").replace(c["name"], "~"), c.get("type_line"), c.get("mana_cost"))
        if key[0]:
            by_text.setdefault(key, []).append(c["name"])
    def resolvable(c: dict) -> str | None:
        """Another name this same card is printed under (or a spelling) that Forge knows."""
        cands = {c["name"], *c["name"].split(" // ")}
        oid = c.get("oracle_id") or (c.get("card_faces") or [{}])[0].get("oracle_id")
        cands |= pnames.get(oid, set())
        for n in cands:
            if norm(n) in forge_norm:
                return n
        return None

    def aliasable(c: dict) -> str | None:
        r = resolvable(c)
        if r:
            return r
        key = ((c.get("oracle_text") or "").replace(c["name"], "~"), c.get("type_line"), c.get("mana_cost"))
        for other in by_text.get(key, []):
            if other != c["name"] and other.lower() in forge_low:
                return other
        return None
    pools = {"all_cards": allc,
             "playable_paper": [c for c in allc if playable(c)],
             "legal_in_any_format": [c for c in allc if any(v in ("legal", "restricted") for v in (c.get("legalities") or {}).values())],
             "playable_paper_or_digital": [c for c in allc if c.get("set_type") not in ("funny", "memorabilia", "token", "minigame")],
             "commander_legal": [c for c in allc
                                 if c.get("legalities", {}).get("commander") in ("legal", "restricted")]}
    out = {"forge_scripts": len(forge)}
    for label, pool in pools.items():
        missing = []
        for c in pool:
            full = c["name"]
            if full.lower() not in forge_low and full.split(" // ")[0].lower() not in forge_low:
                missing.append({"name": full, "set": c.get("set"), "set_type": c.get("set_type"),
                                "games": c.get("games"), "type_line": c.get("type_line"),
                                "released_at": c.get("released_at"), "layout": c.get("layout")})
        (OUT / f"forge-missing-{label}.json").write_text(json.dumps(missing, indent=1))
        n = len(pool)
        byname = {c["name"]: c for c in pool}
        aliases = {m["name"]: a for m in missing if (a := aliasable(byname[m["name"]]))}
        left = [m["name"] for m in missing if m["name"] not in aliases]
        out[label] = {"pool": n, "missing": len(missing),
                      "covered_pct": round(100 * (n - len(missing)) / n, 2),
                      "aliasable": len(aliases),
                      "covered_pct_with_aliases": round(100 * (n - len(left)) / n, 2)}
        if label != "all_cards":
            print(f"{label} STILL MISSING ({len(left)}): " + " | ".join(
                f"{x} [{byname[x].get('set')}/{byname[x].get('set_type')}]" for x in left))
            print(f"{label} ALIASES: " + " | ".join(f"{k} -> {v}" for k, v in list(aliases.items())[:80]))
    return out


def group_report() -> None:
    """Print the missing cards by group (the artifact is not reachable from every reader)."""
    import collections
    allm = json.loads((OUT / "forge-missing-all_cards.json").read_text())
    cmd = {m["name"] for m in json.loads((OUT / "forge-missing-commander_legal.json").read_text())}

    def group(m: dict) -> str:
        games = m.get("games") or []
        if m.get("set_type") == "funny":
            return "un-set / playtest / acorn (funny)"
        if "paper" not in games:
            return "digital-only (Arena/MTGO)"
        if m.get("set_type") in ("memorabilia", "token", "minigame"):
            return "memorabilia / minigame"
        if m.get("layout") in ("adventure", "flip", "split", "transform", "modal_dfc", "meld", "prepare"):
            return "paper, multi-face (possible name-matching miss)"
        return "paper, other"
    g = collections.defaultdict(list)
    for m in allm:
        g[group(m)].append(m)
    print("MISSING BY GROUP")
    for k, v in sorted(g.items(), key=lambda kv: -len(kv[1])):
        st = collections.Counter(x.get("set_type") for x in v).most_common(6)
        print(f"== {k}: {len(v)}  set_types={st}")
        names = [f"{x['name']} [{x.get('set')}/{x.get('layout')}/{x.get('released_at')}]" for x in v]
        print("   " + " | ".join(names[:400]))
    print("COMMANDER-LEGAL MISSING (" + str(len(cmd)) + "): " + " | ".join(sorted(cmd)))


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

    if os.environ.get("SPIKE_MODE") == "coverage":
        (OUT / "summary.json").write_text(json.dumps(summary, indent=1))
        (OUT / "summary.md").write_text("# Forge coverage\n\n" + json.dumps(summary["coverage"], indent=1) + "\n")
        print(json.dumps(summary["coverage"], indent=1))
        return

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
