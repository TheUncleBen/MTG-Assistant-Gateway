# Deck report: Immortal Reckoning

| | |
|---|---|
| Deck | [Immortal Reckoning](https://archidekt.com/decks/42) |
| Commander | Liesa, Forgotten Archangel |
| Run | 2025-10-28 12:00 UTC |
| Status | Complete |
| Games | 300, through turn 10 |
| Report | [Open the report](https://mtg.example.org/history/reports/rep_test) |

## Summary

- Quick solo test — Commander cast (median turn): Turn 5; 40 damage dealt by turn 10: 57%.
- Forge games: Immortal Reckoning won 4 of 10 (40%).
- Forge solo test: won by turn 10 in 213 of 300 games (71%).
- Validation: issues found.

## Quick solo test (300 games, no opponents)

- Commander cast (median turn): Turn 5 — cast in 89% of games by turn 10
- 40 damage dealt by turn 10: 57% (95% interval 51–62%) — median turn 10 when reached
- Lethal power on board by turn 10: 71% (95% interval 66–76%) — median turn 9 when reached
- Damage by turn 10 (average): 33.2 — 33.2 of it in combat
- Games with a mulligan: 50% (95% interval 44–55%) — kept 6.7 cards with 3.2 lands
- Every land drop through turn 5: 62% (95% interval 57–68%)
- Lands on turn 5 (average): 4.5 — 5.4 mana available

## Forge games (against precons)

- Games played: 10 of 10.
- Immortal Reckoning: 4 wins, 40% (95% interval 17% to 69%). Lost by: life total 3, commander damage 2.
- Riders of the Long Name Precon Deck: 3 wins, 30% (95% interval 11% to 60%). Lost by: life total 6.
- Precon 2: 1 win, 10% (95% interval 2% to 40%). Lost by: life total 8.
- Precon 3: 1 win, 10% (95% interval 2% to 40%). Lost by: life total 7, poison 1.
- Draws: 1, 1 of them stopped at the time limit.
- Average game: 141.2 seconds, median 128.0 seconds.
- Median game length: turn 12 (from turn 8 to 21).
- Seed: 7 (kept for the record).
- Forge does not always replay the same games from the same seed, so compare runs by their win rates and 95% intervals, not game by game.

## Forge solo test (full rules, opponents who do nothing)

- Games: 300, each counted up to your turn 10
- Won by turn 10: 213 of 300 (71%, 95% interval 66–76%)
- Median winning turn: 8 (turns 6 to 10)
- Commander cast (median turn): 3
- First opponent out (median turn): 7
- Mulligans: 150 of 300 games
- Lands played each turn (average, from turn 1): 1, 2, 2.9, 3.8, 4.6, 5.3, 5.9, 6.4, 6.8, 7.2
- Spells cast each turn (average, from turn 1): 0.6, 1.9, 3.6, 5.6, 7.8, 10.1, 12.4, 14.6, 16.9, 19.1
- Damage dealt each turn (average, from turn 1): 0, 0.3, 1.8, 4.6, 8.9, 13.7, 19.8, 26.1, 31.9, 37.4
- Left out: 4 games that Forge's time limit stopped before turn 10 or a win.
- Cards Forge could not play: Mirror of the Forebears, Grist, the Hunger Tide // Grist, Pernicious Deed

## What the quick solo test leaves out

Played with their effect: 49 of 98 cards (50% of the deck). Known: 66 of 98 cards (67% of the deck), counting the cards played with their effect and those that need opponents. The rest are listed below.

**Played without their effect: 32 of 98 cards (33% of the deck).**

The solo test found these cards and their exact text, but has no description yet of what they do, so it drew and cast them without their effect. Whatever they would do in a real game is missing from the numbers above. Your assistant may be able to describe some of them from their exact text, in a form the solo test can play. Many cards need things the solo test does not model, such as opponents to interact with.

Aegis of the Gods, Ajani, Caller of the Pride, Cathars' Crusade, Cathedral Sanctifier, Cleric Class, Desecration Demon, Doomsday Excruciator, Ensnaring Bridge, Eternal Thirst, Feign Death, Fetid Imp, Flaming Fist, Gift of Immortality, Goldnight Redeemer, Grim Discovery, Guardian of the Gateless, Holy Day, Indulgent Tormentor, Liesa, Forgotten Archangel, Liliana Vess, Mark of the Vampire, Pacifism, Pestilence Demon, Platinum Angel, Ranger's Hawk, Rite of Consumption, Safe Passage, Shield of the Avatar, Staff of the Sun Magus, Sun Titan, Unhallowed Pact, Vampire Spawn

**Needs opponents: 17 of 98 cards (17% of the deck).**

The simulator plays against opponents who do nothing, so removal, board wipes, counterspells and protection have nothing to act on. Spells that only do that stayed in hand. Creatures and other permanents among them were still cast, and played the parts of their text the simulator can read.

- Removal and other interaction: Avatar of Woe, Banishing Light, Dawnbringer Cleric, Divine Verdict, Doom Blade, Grim Bounty, Keening Apparition, Murder, Ob Nixilis Reignited, Portable Hole, Trapped in the Screen
- Other effects the simulator does not model: Aetherflux Reservoir, Ascend from Avernus, Minimus Containment, Norn's Annex, Shrine of Loyal Legions, Torment of Hailfire

**Played with their effect but never mattered: 25 of 98 cards (26% of the deck).**

These cards were understood, drawn and played, and nothing they do changed a solo test game. A solo test game has no opponent and no interaction, so this says nothing about how the card does at a real table.

Black Dragon Gate, Charcoal Diamond, Daggerdrome Imp, Dungeon Map, Evolving Wilds, Leather Armor, Leechridden Swamp, Lightning Greaves, Lotus Field, Marble Diamond, Markov Patrician, Mask of Avacyn, Nephalia Academy, Opaline Unicorn, Orzhov Guildgate, Orzhov Keyrune, Plains, Plate Armor, Seraph of Dawn, Swamp, The Soul Stone, Transguild Promenade, Typhoid Rats, Vampire Nighthawk, Vault of the Archangel

**Shortcuts the model takes: 6.**

Simplifications applied to these cards; they play a little better or worse than in a real game.

- Treated as producing any color: Lotus Field, Opaline Unicorn, Transguild Promenade
- Mana creatures can tap for mana the turn they arrive: Opaline Unicorn
- Equipment text beyond its bonus is ignored: Leather Armor, Plate Armor
- Fetch lands find a land that enters tapped, in your main phase: Evolving Wilds
- Hybrid and Phyrexian costs paid with one color: Norn's Annex
- Extra card text ignored: Black Dragon Gate, Dungeon Map, Leechridden Swamp, Lotus Field, Nephalia Academy, Orzhov Keyrune, The Soul Stone, Transguild Promenade, Vault of the Archangel

## Validation: issues found

- 2 card(s) not found on Scryfall:
  - Enduring Angel
  - Elbrus, the Binding Blade

### Gateway checks

- Singleton: no card has more than one copy.
- Copies are counted by name over every line; basic lands and the cards a deck may hold any number of are exempt. Banned cards and the other format rules: Deck stats, on the deck's page.

## Deck statistics

- Cards: 100
- Lands: 34
- Average mana value: 3.2
- Mana curve: 1: 9, 2: 17

## Raw output

The research service's own output is kept with the report: https://mtg.example.org/history/reports/rep_test
