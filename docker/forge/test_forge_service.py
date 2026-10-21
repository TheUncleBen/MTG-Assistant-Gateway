"""Offline tests of the Forge job service (no Java needed; a stand-in plays the games): name matching,
request checks, parsing, and how a job's games are cut into slices and played."""

import os
import queue
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

_names = Path(tempfile.mkdtemp()) / "names.txt"
_names.write_text("Liesa, Forgotten Archangel\nFire\nIce\nLim-Dûl's Vault\nSol Ring\nForest\n", "utf-8")
os.environ["FORGE_NAMES"] = str(_names)
_precons = _names.parent / "precons.json"
_precons.write_text(
    '[{"name": "Test Precon", "release_date": "2026-01-01", "commander": ["Liesa, Forgotten Archangel"],'
    ' "main": [[99, "Forest"]]}]',
    "utf-8",
)
os.environ["FORGE_PRECONS"] = str(_precons)
sys.path.insert(0, str(Path(__file__).parent))
import forge_service as fs  # noqa: E402


class Names(unittest.TestCase):
    def test_case_accents_and_quotes(self):
        self.assertEqual(fs.resolve("liesa, forgotten archangel"), "Liesa, Forgotten Archangel")
        self.assertEqual(fs.resolve("Lim-Dul’s Vault"), "Lim-Dûl's Vault")
        self.assertIsNone(fs.resolve("Not A Card"))

    def test_split_card_by_front_face(self):
        self.assertEqual(fs.resolve("Fire // Ice"), "Fire")


class Validate(unittest.TestCase):
    def deck(self, **kw):
        return {"commander": ["Liesa, Forgotten Archangel"], "main": [[1, "Sol Ring"], [98, "Forest"]], **kw}

    def test_good_request(self):
        decks, games, fmt, unknown = fs.validate({"games": 3, "decks": [self.deck(), self.deck()]})
        self.assertEqual((games, fmt, unknown), (3, "Commander", []))
        self.assertEqual(decks[0]["main"][0], [1, "Sol Ring"])

    def test_precon_opponent(self):
        decks = fs.validate({"games": 1, "decks": [self.deck(), {"precon": "Test Precon"}]})[0]
        self.assertEqual(decks[1]["main"], [[99, "Forest"]])
        with self.assertRaises(ValueError):
            fs.validate({"games": 1, "decks": [self.deck(), {"precon": "Nope"}]})

    def test_unknown_cards_are_listed(self):
        bad = self.deck(main=[[1, "Not A Card"]])
        self.assertEqual(fs.validate({"games": 1, "decks": [bad, self.deck()]})[3], ["Not A Card"])

    def test_limits(self):
        for body in (
            {"games": 0, "decks": [self.deck(), self.deck()]},
            {"games": fs.MAX_GAMES + 1, "decks": [self.deck(), self.deck()]},
            {"games": 1, "decks": [self.deck()]},
            {"games": 1, "decks": [self.deck()] * 5},
            {"games": 1, "format": "Vintage", "decks": [self.deck(), self.deck()]},
            {"games": 1, "decks": [self.deck(commander=[]), self.deck()]},
            {"games": 1, "decks": [self.deck(main=[["1", "Sol Ring"]]), self.deck()]},
        ):
            with self.assertRaises(ValueError, msg=body):
                fs.validate(body)


class Output(unittest.TestCase):
    def test_deck_file(self):
        deck = {"commander": ["Liesa, Forgotten Archangel"], "main": [[1, "Sol Ring"]]}
        self.assertEqual(
            fs.deck_file(2, deck, "Commander").splitlines(),
            ["[metadata]", "Name=D2", "[Commander]", "1 Liesa, Forgotten Archangel", "[Main]", "1 Sol Ring"],
        )

    def test_parse_win_draw_and_unknown(self):
        win = fs.parse_result("Game 3 ended in 51234 ms. Ai(2)-D2 has won!")
        self.assertEqual((win["game"], win["ms"], win["winner"], win["draw"]), (3, 51234, 2, False))
        draw = fs.parse_result("Game Result: Game 1 ended in a Draw! Took 600012 ms.")
        self.assertEqual((draw["game"], draw["ms"], draw["winner"], draw["draw"]), (1, 600012, None, True))
        self.assertIn("unparsed", fs.parse_result("Game 2 ended in 5 ms. something else"))
        self.assertIsNone(fs.parse_result("Turn 4 (Ai(1)-D1)"))

    def test_outcome_lines(self):
        # Lines as Forge 2.0.15 printed them in the arm64 smoke test (forge-image run 38156582933).
        self.assertEqual(fs.OUTCOME_TURN.search("Game Outcome: Turn 18").group("turn"), "18")
        lost = fs.OUTCOME_LOST.search(
            "Game Outcome: Ai(2)-D2 has lost due to accumulation of 21 damage from generals"
        )
        self.assertEqual(
            (lost.group("seat"), lost.group("why")), ("2", "accumulation of 21 damage from generals")
        )
        lost = fs.OUTCOME_LOST.search("Game Outcome: Ai(1)-D1 has lost because life total reached 0")
        self.assertEqual((lost.group("seat"), lost.group("why")), ("1", "life total reached 0"))
        self.assertIsNone(
            fs.OUTCOME_LOST.search("Game Outcome: Ai(4)-D4 has won because all opponents have lost")
        )


class Places(unittest.TestCase):
    def test_places_follow_the_machine(self):
        gib = 2**30
        self.assertEqual(fs.places_for(2, 1536 * 2**20), 1)  # the stack's default limits
        self.assertEqual(fs.places_for(4, 8 * gib), 2)  # a Pi 4 or 5 with 8 GB, no limits
        self.assertEqual(fs.places_for(4, 2 * gib), 1)  # memory decides
        self.assertEqual(fs.places_for(16, 32 * gib), 8)  # a 16-CPU, 32 GB machine: CPUs decide
        self.assertEqual(fs.places_for(8, 16 * gib), 4)
        self.assertEqual(fs.places_for(32, 64 * gib), 16)
        self.assertEqual(fs.places_for(64, 256 * gib), 16)  # never more than 16
        self.assertEqual(fs.places_for(32, 5 * gib), 4)  # memory decides
        self.assertEqual(fs.places_for(1, 16 * gib), 1)  # never fewer than 1
        self.assertEqual(fs.places_for(8, None), 1)  # memory unknown: one at a time

    def test_the_setting_wins_over_the_machine(self):
        self.assertEqual(fs.max_parallel("3"), (3, "FORGE_MAX_PARALLEL"))
        self.assertEqual(fs.max_parallel("9"), (9, "FORGE_MAX_PARALLEL"))
        self.assertEqual(fs.max_parallel("16"), (16, "FORGE_MAX_PARALLEL"))
        self.assertEqual(fs.max_parallel("40"), (16, "FORGE_MAX_PARALLEL"))
        self.assertEqual(fs.max_parallel("0"), (1, "FORGE_MAX_PARALLEL"))
        places, source = fs.max_parallel("")
        self.assertEqual(source, "machine")
        self.assertTrue(1 <= places <= 16)
        self.assertEqual(fs.max_parallel(None)[1], "machine")

    def test_the_container_limits_are_read(self):
        reads = {
            "/sys/fs/cgroup/cpu.max": "200000 100000",
            "/sys/fs/cgroup/memory.max": str(1536 * 2**20),
        }
        saved, fs._read = fs._read, lambda path: reads.get(path, "")
        try:
            self.assertEqual(fs.machine_cpus(), 2.0)
            self.assertEqual(fs.machine_memory(), 1536 * 2**20)
            reads["/sys/fs/cgroup/cpu.max"] = "max 100000"  # no CPU limit: the machine's count
            self.assertEqual(fs.machine_cpus(), float(os.cpu_count() or 1))
            reads["/sys/fs/cgroup/memory.max"] = "max"
            reads["/proc/meminfo"] = "MemTotal:        8000000 kB\nMemFree: 1 kB"
            self.assertEqual(fs.machine_memory(), 8000000 * 1024)
        finally:
            fs._read = saved

    def test_the_memory_budget_stands_in_for_a_missing_limit(self):
        reads = {"/proc/meminfo": "MemTotal:        8000000 kB"}  # no memory.max: no cgroup limit
        saved, fs._read = fs._read, lambda path: reads.get(path, "")
        saved_budget = fs.MEMORY_BUDGET
        try:
            fs.MEMORY_BUDGET = "1536M"
            self.assertEqual(fs.machine_memory(), 1536 * 2**20)
            self.assertEqual(fs.places_for(2, fs.machine_memory()), 1)
            reads["/sys/fs/cgroup/memory.max"] = str(3 * 2**30)  # a real limit wins over the budget
            self.assertEqual(fs.machine_memory(), 3 * 2**30)
            del reads["/sys/fs/cgroup/memory.max"]
            fs.MEMORY_BUDGET = ""
            self.assertEqual(fs.machine_memory(), 8000000 * 1024)
        finally:
            fs._read, fs.MEMORY_BUDGET = saved, saved_budget

    def test_sizes(self):
        self.assertEqual(fs.size_bytes("1536M"), 1536 * 2**20)
        self.assertEqual(fs.size_bytes("2g"), 2 * 2**30)
        self.assertEqual(fs.size_bytes("800000k"), 800000 * 2**10)
        self.assertEqual(fs.size_bytes("1000"), 1000)
        self.assertEqual(fs.size_bytes("1.5g"), int(1.5 * 2**30))
        self.assertEqual(fs.size_bytes("1.5GB"), int(1.5 * 2**30))
        self.assertIsNone(fs.size_bytes(""))
        self.assertIsNone(fs.size_bytes("lots"))
        self.assertIsNone(fs.size_bytes("1,5g"))

    def test_a_budget_that_is_not_a_size_gives_one_place_not_the_whole_machine(self):
        reads = {"/proc/meminfo": "MemTotal:        8000000 kB"}
        saved, fs._read = fs._read, lambda path: reads.get(path, "")
        saved_budget = fs.MEMORY_BUDGET
        try:
            fs.MEMORY_BUDGET = "1.5 gigs"
            self.assertIsNone(fs.machine_memory())
            self.assertEqual(fs.places_for(8, fs.machine_memory()), 1)
            fs.MEMORY_BUDGET = "1.5g"
            self.assertEqual(fs.machine_memory(), int(1.5 * 2**30))
        finally:
            fs._read, fs.MEMORY_BUDGET = saved, saved_budget

    def test_out_of_memory_scores_forge_cannot_set_are_left_out(self):
        self.assertEqual(fs.oom_score("1000"), (1000, ""))
        self.assertEqual(fs.oom_score(" 500 "), (500, ""))
        for text in ("0", "-500", "1001", "high", ""):
            score, why = fs.oom_score(text)
            self.assertEqual(score, 0, text)
            self.assertIn("FORGE_OOM_SCORE_ADJ", why)


class Slices(unittest.TestCase):
    def test_one_game_per_group(self):
        self.assertEqual(fs.SLICE_GAMES, 1)
        self.assertEqual(fs.slices(1), [1])
        self.assertEqual(fs.slices(4), [1, 1, 1, 1])
        for games in range(1, fs.MAX_GAMES + 1):
            self.assertEqual(fs.slices(games), [1] * games)

    def test_cut_by_the_number_of_games_only(self):
        saved, fs.SLICE_GAMES = fs.SLICE_GAMES, 5
        try:
            self.assertEqual(fs.slices(1), [1])
            self.assertEqual(fs.slices(5), [5])
            self.assertEqual(fs.slices(7), [4, 3])
            self.assertEqual(fs.slices(10), [5, 5])
            self.assertEqual(fs.slices(12), [4, 4, 4])
            self.assertEqual(fs.slices(50), [5] * 10)
            for games in range(1, fs.MAX_GAMES + 1):
                self.assertEqual(sum(fs.slices(games)), games)
        finally:
            fs.SLICE_GAMES = saved

    def test_seeds(self):
        self.assertEqual(fs.slice_seed(42, 0), 42)  # one slice plays exactly as before slices
        self.assertIsNone(fs.slice_seed(None, 3))
        seeds = [fs.slice_seed(42, i) for i in range(10)]
        self.assertEqual(len(set(seeds)), 10)
        self.assertTrue(all(0 <= s < 2**63 for s in seeds))
        self.assertEqual(seeds, [fs.slice_seed(42, i) for i in range(10)])


# A stand-in for `java`: prints one result line per game with the seed it was given, so the tests
# see which slice played which game. FAKE_SLOW makes a slice wait (to see slices overlap). It logs
# the home a real Java would use: -Duser.home when given, else the user account's, never $HOME
# (checked on Java 21: HOME=/tmp/x still gives user.home=/root).
FAKE_JAVA = """#!/usr/bin/env python3
import os, pwd, sys, time
a = sys.argv
n = int(a[a.index("-n") + 1])
seed = a[a.index("-s") + 1] if "-s" in a else "none"
print("Ai(1)-D1 vs Ai(2)-D2 - %d games of Commander seed %s" % (n, seed), flush=True)
log = os.path.join(os.environ["FAKE_LOG"], "start-%s-%s" % (seed, time.time()))
given = [x[len("-Duser.home="):] for x in a if x.startswith("-Duser.home=")]
open(log, "w").write(given[0] if given else pwd.getpwuid(os.getuid()).pw_dir)
if os.environ.get("FAKE_FLAGS"):  # what the memory guards gave this Java
    oom = open("/proc/self/oom_score_adj").read().strip()
    open(os.environ["FAKE_FLAGS"], "w").write(" ".join([oom, *(x for x in a if x.startswith("-X"))]))
time.sleep(float(os.environ.get("FAKE_SLOW", "0")))
if "-q" not in a:  # a solo test reads the game log: D1 plays second, hits D2 for 10 a turn
    life = 40
    for t in range(1, 40):
        print("Turn: Turn %d (Ai(2)-D2)" % (2 * t - 1), flush=True)
        print("Turn: Turn %d (Ai(1)-D1)" % (2 * t), flush=True)
        print("Land: Ai(1)-D1 played Forest (%d)" % t, flush=True)
        if t == 2:
            print("Add To Stack: Ai(1)-D1 cast Liesa, Forgotten Archangel", flush=True)
        if t >= 3:
            print("Damage: Grizzly Bears (9) deals 10 combat damage to Ai(2)-D2.", flush=True)
            print("Life: Life: Ai(2)-D2 %d > %d" % (life, life - 10), flush=True)
            life -= 10
        if life <= 0:
            print("Game Outcome: Turn %d" % (2 * t), flush=True)
            print("Game Outcome: Ai(2)-D2 has lost because life total reached 0", flush=True)
            print("Game Result: Game 1 ended in 900 ms. Ai(1)-D1 has won!", flush=True)
            sys.exit(0)
for g in range(1, n + 1):
    w = 1 + g % 2
    print("Game Outcome: Turn %d" % (5 + g), flush=True)
    print("Game Outcome: Ai(%d)-D%d has lost because life total reached 0" % (3 - w, 3 - w), flush=True)
    print("Game Result: Game %d ended in %d ms. Ai(%d)-D%d has won!" % (g, 1000 + g, w, w), flush=True)
"""


class Solo(unittest.TestCase):
    DECK = {"commander": ["Liesa, Forgotten Archangel"], "main": [[99, "Forest"]]}

    def test_a_solo_request_lists_one_deck(self):
        body = {"games": 2, "decks": [self.DECK], "solo": {}}
        decks, games, fmt, unknown = fs.validate(body)
        self.assertEqual((len(decks), games, fmt, unknown), (1, 2, "Commander", []))
        self.assertEqual(fs.validate_solo(body, fmt), {"opponents": 3, "until_turn": 10})
        self.assertEqual(fs.validate_solo({"solo": {}}, "Constructed")["opponents"], 1)
        self.assertIsNone(fs.validate_solo({}, "Commander"))
        for bad in (
            {"games": 1, "decks": [self.DECK, self.DECK], "solo": {}},
            {"games": 1, "decks": [], "solo": {}},
        ):
            with self.assertRaises(ValueError):
                fs.validate(bad)
        for solo in ({"opponents": 4}, {"opponents": 0}, {"until_turn": 31}, {"until_turn": True}, "yes"):
            with self.assertRaises(ValueError, msg=solo):
                fs.validate_solo({"solo": solo}, "Commander")

    def test_the_opponents_do_nothing(self):
        job = fs.Job([self.DECK], 1, "Commander", 1, 1, {"opponents": 2, "until_turn": 5})
        self.assertEqual(len(job.decks), 3)
        self.assertEqual(job.decks[1], {"commander": ["Progenitus"], "main": [[99, "Wastes"]]})
        self.assertEqual(fs.solo_opponent("Constructed"), {"commander": [], "main": [[60, "Wastes"]]})
        self.assertEqual(job.view()["solo"], {"opponents": 2, "until_turn": 5})

    def test_the_log_is_read_by_the_decks_own_turns(self):
        # Lines as Forge 2.0.15 prints them; D1 is the third seat here, so its first turn is game turn 3.
        game = fs.SoloGame(["Anax, Hardened in the Forge"], 3, 40, 10)
        for line in (
            "Mulligan: Ai(1)-D1 has kept a hand of 7 cards",
            "Turn: Turn 1 (Ai(2)-D2)",
            "Turn: Turn 2 (Ai(3)-D3)",
            "Turn: Turn 3 (Ai(1)-D1)",
            "Land: Ai(1)-D1 played Mountain (12)",
            "Turn: Turn 4 (Ai(4)-D4)",
            "Turn: Turn 5 (Ai(2)-D2)",
            "Turn: Turn 6 (Ai(3)-D3)",
            "Turn: Turn 7 (Ai(1)-D1)",
            "Land: Ai(1)-D1 played Mountain (40)",
            "Add To Stack: Ai(1)-D1 cast Anax, Hardened in the Forge",
            "Add To Stack: Ai(1)-D1 cast Shock targeting [Ai(4)-D4]",
            "Life: Life: Ai(4)-D4 40 > 38",
            "Damage: Anax, Hardened in the Forge (100) deals 21 combat damage to Ai(2)-D2.",
            "Life: Life: Ai(2)-D2 40 > 19",
        ):
            game.feed(line)
        view = game.view()
        self.assertEqual(view["turns"], 2)
        self.assertEqual(view["kept"], 7)
        self.assertEqual(view["commander_cast_turn"], 2)
        self.assertEqual(view["lands_by_turn"], [1, 1])
        self.assertEqual(view["casts_by_turn"], [0, 2])
        self.assertEqual(view["damage_by_turn"], [0, 23])
        self.assertEqual((view["first_out_turn"], view["won_turn"]), (2, None))  # 21 commander damage
        self.assertEqual(view["commander_damage"], 21)


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "bin").mkdir()
        java = self.tmp / "bin" / "java"
        java.write_text(FAKE_JAVA, "utf-8")
        java.chmod(java.stat().st_mode | stat.S_IEXEC)
        (self.tmp / "forge").mkdir()
        (self.tmp / "forge" / "forge-gui-desktop-2.0.15-jar-with-dependencies.jar").write_text("")
        (self.tmp / "log").mkdir()
        (self.tmp / "home").mkdir()
        self.env = {
            "PATH": f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "FAKE_LOG": str(self.tmp / "log"),
            "HOME": str(self.tmp / "home"),
        }
        self.old = {k: os.environ.get(k) for k in [*self.env, "FAKE_SLOW"]}
        os.environ.update(self.env)
        self.home, fs.FORGE_HOME = fs.FORGE_HOME, self.tmp / "forge"
        # one place unless a test sets more (the default follows the test machine), and groups of
        # up to 5 games so these tests see how a job's games are cut and played in several groups
        fs.MAX_PARALLEL = 1
        fs.FREE_PLACES[:] = [0]
        self.slice_games, fs.SLICE_GAMES = fs.SLICE_GAMES, 5

    def tearDown(self):
        fs.FORGE_HOME = self.home
        fs.SLICE_GAMES = self.slice_games
        fs.MAX_PARALLEL = 1
        fs.FREE_PLACES[:] = [0]
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run_job(self, games, seed=42, parallel=1):
        deck = {"commander": ["Liesa, Forgotten Archangel"], "main": [[99, "Forest"]]}
        job = fs.Job([deck, deck], games, "Commander", seed, parallel)
        fs.run_job(job)
        return job

    def solo_job(self, until_turn):
        deck = {"commander": ["Liesa, Forgotten Archangel"], "main": [[99, "Forest"]]}
        job = fs.Job([deck], 1, "Commander", 42, 1, {"opponents": 1, "until_turn": until_turn})
        fs.run_job(job)
        return job.view()

    def test_a_solo_game_that_wins_in_time(self):
        view = self.solo_job(10)
        self.assertEqual(view["state"], "done")
        (game,) = view["results"]
        self.assertEqual(game["winner"], 1)
        self.assertEqual(game["solo"]["won_turn"], 6)  # 10 a turn from turn 3: 40 life by turn 6
        self.assertEqual(game["solo"]["damage_by_turn"], [0, 0, 10, 10, 10, 10])
        self.assertEqual(game["solo"]["commander_cast_turn"], 2)

    def test_a_solo_game_stops_after_its_last_turn(self):
        view = self.solo_job(4)
        self.assertEqual(view["state"], "done")
        (game,) = view["results"]
        self.assertTrue(game["turn_limit"])
        self.assertIsNone(game["winner"])
        self.assertEqual(game["solo"]["turns"], 4)
        self.assertEqual(game["solo"]["damage_by_turn"], [0, 0, 10, 10])
        self.assertIsNone(game["solo"]["won_turn"])

    def test_with_one_game_per_group_every_game_can_play_at_once(self):
        fs.SLICE_GAMES = 1
        fs.MAX_PARALLEL = 4
        fs.FREE_PLACES[:] = [0, 1, 2, 3]
        job = self.run_job(4, parallel=4)
        view = job.view()
        self.assertEqual(view["state"], "done")
        self.assertEqual([s["games"] for s in view["slices"]], [1, 1, 1, 1])
        self.assertEqual(view["parallel"], 4)
        self.assertEqual(sorted(r["game"] for r in view["results"]), [1, 2, 3, 4])

    def test_same_seed_same_games_at_any_parallel_setting(self):
        one = self.run_job(12).view()
        fs.MAX_PARALLEL = 3
        three = self.run_job(12, parallel=3).view()
        self.assertEqual((one["state"], three["state"]), ("done", "done"))
        self.assertEqual((one["parallel"], three["parallel"]), (1, 3))
        self.assertEqual([r["game"] for r in one["results"]], list(range(1, 13)))
        self.assertEqual(one["results"], three["results"])
        self.assertEqual([s["seed"] for s in one["slices"]], [42, fs.slice_seed(42, 1), fs.slice_seed(42, 2)])
        self.assertEqual([s["state"] for s in three["slices"]], ["done"] * 3)
        self.assertIn("seed 42", one["header"])
        first = one["results"][0]
        self.assertEqual((first["turn"], first["lost"]), (6, {"1": "life total reached 0"}))

    @unittest.skipUnless(shutil.which("choom"), "needs util-linux choom, as the image has")
    def test_java_runs_with_its_memory_guards(self):
        flags = self.tmp / "flags"
        os.environ["FAKE_FLAGS"] = str(flags)
        try:
            self.assertEqual(self.run_job(1).view()["state"], "done")
        finally:
            os.environ.pop("FAKE_FLAGS")
        oom, *java = flags.read_text().split()
        self.assertEqual(oom, str(fs.OOM_SCORE_ADJ))  # the kernel's first pick when memory runs out
        self.assertIn(f"-Xmx{fs.XMX}", java)
        self.assertIn(f"-XX:MaxMetaspaceSize={fs.MAX_METASPACE}", java)
        self.assertIn("-XX:+UseSerialGC", java)
        self.assertIn(f"-XX:SharedArchiveFile={self.tmp / 'home' / f'forge-{fs.VERSION}.jsa'}", java)

    def test_java_starts_without_choom_when_the_score_is_off(self):
        flags = self.tmp / "flags"
        os.environ["FAKE_FLAGS"] = str(flags)
        saved, fs.OOM_SCORE_ADJ = fs.OOM_SCORE_ADJ, fs.oom_score("-500")[0]
        try:
            self.assertEqual(self.run_job(1).view()["state"], "done")
        finally:
            os.environ.pop("FAKE_FLAGS")
            fs.OOM_SCORE_ADJ = saved
        oom = flags.read_text().split()[0]
        self.assertNotEqual(oom, "1000")  # no choom: the score the service itself runs with

    def test_parallel_is_capped_and_extra_places_get_their_own_home(self):
        fs.MAX_PARALLEL = 2
        os.environ["FAKE_SLOW"] = "0.5"
        job = self.run_job(15, parallel=4)
        self.assertEqual(job.parallel, 2)
        homes = sorted(p.read_text() for p in (self.tmp / "log").iterdir())
        self.assertEqual(len(homes), 3)
        self.assertIn(str(self.tmp / "home"), homes)
        self.assertIn(str(self.tmp / "home" / "slot-1"), homes)
        self.assertEqual(job.view()["games_done"], 15)
        starts = sorted(float(p.name.split("-")[-1]) for p in (self.tmp / "log").iterdir())
        self.assertLess(starts[1] - starts[0], 0.4)  # the first two slices played at once

    def test_each_place_has_its_own_forge_home_with_lazy_card_loading(self):
        # Parallel Forge processes shared the account's home folder, as
        # Java ignores $HOME; each place now names its own, and Forge loads card scripts lazily.
        fs.MAX_PARALLEL = 2
        os.environ["FAKE_SLOW"] = "0.3"
        home = self.tmp / "home"
        prefs = home / ".forge" / "preferences" / "forge.preferences"
        prefs.parent.mkdir(parents=True)
        prefs.write_text("UI_THEME=Default\nLOAD_CARD_SCRIPTS_LAZILY=false\n", "utf-8")
        self.run_job(10, parallel=2)
        homes = sorted(p.read_text() for p in (self.tmp / "log").iterdir())
        self.assertEqual(homes, [str(home), str(home / "slot-1")])
        # an existing preferences file keeps its other lines; the one setting is replaced
        self.assertEqual(prefs.read_text("utf-8"), "UI_THEME=Default\nLOAD_CARD_SCRIPTS_LAZILY=true\n")
        slot = home / "slot-1" / ".forge" / "preferences" / "forge.preferences"
        self.assertEqual(slot.read_text("utf-8"), "LOAD_CARD_SCRIPTS_LAZILY=true\n")
        fs.LAZY_CARDS = False
        try:
            self.assertEqual(fs.place_home(1), home / "slot-1")
        finally:
            fs.LAZY_CARDS = True
        self.assertEqual(slot.read_text("utf-8"), "LOAD_CARD_SCRIPTS_LAZILY=false\n")

    def worker_with(self, places):
        """A worker of its own on a fresh queue, with ``places`` places shared by its jobs."""
        fs.MAX_PARALLEL = places
        fs.FREE_PLACES[:] = list(range(places))
        fs.QUEUE = queue.Queue()
        threading.Thread(target=fs.worker, daemon=True).start()
        return fs.QUEUE

    def wait_done(self, jobs, seconds=20):
        end = time.time() + seconds
        while time.time() < end and any(j.finished is None for j in jobs):
            time.sleep(0.05)
        self.assertTrue(all(j.finished is not None for j in jobs), [j.state for j in jobs])

    def test_jobs_share_the_places_in_order(self):
        # a job that leaves places free (a comparison's first deck) lets the next job
        # play beside it, never more processes than places, and never out of order
        old = fs.QUEUE
        try:
            os.environ["FAKE_SLOW"] = "0.6"
            deck = {"commander": ["Liesa, Forgotten Archangel"], "main": [[99, "Forest"]]}
            q = self.worker_with(2)
            jobs = [fs.Job([deck, deck], 5, "Commander", seed, 1) for seed in (1, 2, 3)]
            for job in jobs:
                q.put(job)
            self.wait_done(jobs)
            self.assertEqual([j.state for j in jobs], ["done"] * 3)
            # the first two started together on their own places; the third waited for one
            self.assertLess(abs(jobs[1].started - jobs[0].started), 0.4)
            self.assertGreater(jobs[2].started - jobs[0].started, 0.5)
            homes = {p.read_text() for p in (self.tmp / "log").iterdir()}
            self.assertEqual(homes, {str(self.tmp / "home"), str(self.tmp / "home" / "slot-1")})
            self.assertEqual(sorted(fs.FREE_PLACES), [0, 1])  # every place given back
            # one place: the second job waits for the whole first one, as before
            for p in (self.tmp / "log").iterdir():
                p.unlink()
            q = self.worker_with(1)
            two = [fs.Job([deck, deck], 10, "Commander", seed, 1) for seed in (4, 5)]
            for job in two:
                q.put(job)
            self.wait_done(two)
            self.assertGreaterEqual(two[1].started, two[0].finished - 0.05)
            self.assertEqual(fs.FREE_PLACES, [0])
        finally:
            fs.QUEUE = old

    def test_a_job_on_every_place_gives_one_back_to_a_job_that_waits(self):
        # a run on every place held them all until its last game; now each
        # of its places lets go after its group while another job waits, and the run goes on
        old = fs.QUEUE
        try:
            os.environ["FAKE_SLOW"] = "0.6"
            deck = {"commander": ["Liesa, Forgotten Archangel"], "main": [[99, "Forest"]]}
            q = self.worker_with(2)
            big = fs.Job([deck, deck], 20, "Commander", 1, 2)  # four groups on both places
            q.put(big)
            time.sleep(0.2)
            small = fs.Job([deck, deck], 5, "Commander", 2, 1)
            q.put(small)
            self.wait_done([big, small])
            self.assertEqual([big.state, small.state], ["done", "done"])
            self.assertEqual(big.view()["games_done"], 20)
            # the small job started when the big one's first groups ended, not after all four
            self.assertLess(small.started - big.started, 1.0)
            self.assertEqual(sorted(fs.FREE_PLACES), [0, 1])
        finally:
            fs.QUEUE = old

    def test_places_giving_back_at_once_leave_one_to_finish_the_run(self):
        # every place sees a job waiting and gives back, each one pausing just after it lets go
        # of the lock: exactly one place stays and the run still plays all its games
        real_lock = threading.Lock
        asked = threading.local()

        def waiting():
            asked.yes = True
            return True

        class PausingLock:
            # pauses after a release that follows a waiting() check, so the other places reach
            # the same check before this one has done anything else
            def __init__(self):
                self.lock = real_lock()

            def acquire(self, *a, **k):
                return self.lock.acquire(*a, **k)

            def release(self):
                self.lock.release()
                if getattr(asked, "yes", False):
                    asked.yes = False
                    time.sleep(0.3)

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *exc):
                self.release()

        fs.SLICE_GAMES = 1
        fs.MAX_PARALLEL = 4
        fs.FREE_PLACES[:] = [0, 1, 2, 3]
        deck = {"commander": ["Liesa, Forgotten Archangel"], "main": [[99, "Forest"]]}
        job = fs.Job([deck, deck], 8, "Commander", 42, 4)
        with mock.patch.object(fs, "waiting", waiting), mock.patch.object(fs.threading, "Lock", PausingLock):
            fs.run_job(job)
        view = job.view()
        self.assertEqual(view["state"], "done")
        self.assertEqual(view["games_done"], 8)
        self.assertEqual(sorted(fs.FREE_PLACES), [0, 1, 2, 3])

    def test_a_job_cancelled_while_waiting_for_a_place_never_starts(self):
        old = fs.QUEUE
        try:
            os.environ["FAKE_SLOW"] = "1.5"
            deck = {"commander": ["Liesa, Forgotten Archangel"], "main": [[99, "Forest"]]}
            q = self.worker_with(1)
            first, second = (fs.Job([deck, deck], 5, "Commander", s, 1) for s in (1, 2))
            q.put(first)
            q.put(second)
            time.sleep(0.3)
            self.assertEqual(len(fs.HELD), 1)  # the second waits for the place, counted as queued
            second.cancelled, second.state = True, "cancelled"
            self.wait_done([first])
            time.sleep(0.3)
            self.assertIsNone(second.started)
            self.assertEqual(len(list((self.tmp / "log").iterdir())), 1)
            self.assertEqual(fs.FREE_PLACES, [0])
        finally:
            fs.QUEUE = old

    def test_one_slice_is_one_process_as_before(self):
        job = self.run_job(5, parallel=4)
        self.assertEqual((job.parallel, len(job.slices)), (1, 1))
        self.assertEqual(len(list((self.tmp / "log").iterdir())), 1)
        self.assertNotIn("[slice", " ".join(job.tail))

    def test_cancel_stops_every_slice(self):
        fs.MAX_PARALLEL = 2
        os.environ["FAKE_SLOW"] = "30"
        deck = {"commander": ["Liesa, Forgotten Archangel"], "main": [[99, "Forest"]]}
        job = fs.Job([deck, deck], 20, "Commander", 1, 2)
        t = threading.Thread(target=fs.run_job, args=(job,))
        t.start()
        # both slices playing: each fake Forge has logged its start (a loaded machine can take
        # longer than the process start itself, so the kill came before the log: a flaky count)
        for _ in range(200):
            if len(job.procs) == 2 and len(list((self.tmp / "log").iterdir())) == 2:
                break
            time.sleep(0.05)
        job.cancelled = True
        fs._kill(job)
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertEqual(job.state, "cancelled")
        self.assertEqual(len(list((self.tmp / "log").iterdir())), 2)  # no further slice started

    def test_no_group_starts_after_the_time_limit(self):
        # A group picked up just as the timer fires never starts its process
        deck = {"commander": ["Liesa, Forgotten Archangel"], "main": [[99, "Forest"]]}
        job = fs.Job([deck, deck], 10, "Commander", 1, 1)
        fs._time_out(job)
        self.assertTrue(job.timed_out)
        self.assertIsNone(fs.run_slice(job, 1, self.tmp, ["D1", "D2"], 0))
        self.assertEqual(job.slice_state[1], "cancelled")
        self.assertEqual(list((self.tmp / "log").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
