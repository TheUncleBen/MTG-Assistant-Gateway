"""Offline tests of the Forge job service (no Java needed): name matching, request checks, parsing."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_names = Path(tempfile.mkdtemp()) / "names.txt"
_names.write_text("Liesa, Forgotten Archangel\nFire\nIce\nLim-Dûl's Vault\nSol Ring\nForest\n", "utf-8")
os.environ["FORGE_NAMES"] = str(_names)
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
        draw = fs.parse_result("Game 1 ended in 900 ms. The game is a draw.")
        self.assertTrue(draw["draw"])
        self.assertIn("unparsed", fs.parse_result("Game 2 ended in 5 ms. something else"))
        self.assertIsNone(fs.parse_result("Turn 4 (Ai(1)-D1)"))


if __name__ == "__main__":
    unittest.main()
