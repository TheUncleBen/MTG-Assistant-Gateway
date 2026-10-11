"""Offline test of the precon bracket estimate written into the Forge image (precons.py)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import precons  # noqa: E402


class Bracket(unittest.TestCase):
    def test_game_changers_set_the_bracket(self):
        changers = {
            precons.front(n)
            for n in ("Rhystic Study", "Smothering Tithe", "Cyclonic Rift", "Fierce Guardianship")
        }
        self.assertEqual(precons.bracket_of(["Sol Ring", "Forest"], changers), ([], 2))
        self.assertEqual(precons.bracket_of(["Rhystic Study", "Forest"], changers), (["Rhystic Study"], 3))
        four = ["Rhystic Study", "Smothering Tithe", "Cyclonic Rift", "Fierce Guardianship"]
        self.assertEqual(precons.bracket_of(four, changers)[1], 4)
        # a two-faced name is matched by its front face, whatever the case
        self.assertEqual(precons.bracket_of(["rhystic study // Back"], changers)[1], 3)


if __name__ == "__main__":
    unittest.main()
