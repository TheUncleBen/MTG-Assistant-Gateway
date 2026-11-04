# What hardware you need

The gateway runs on any 64-bit ARM or x86 machine with Docker. Forge games have been timed on a
Raspberry Pi 5; a Pi 4 has not been timed.
Forge, the game engine, is the only part that needs real power. It is on in the example files;
turn it off on a machine below the "With Forge on" minimum in the table below
([how](DEPLOY.md#forge-games-on-by-default)).

## Minimum and recommended

| | Minimum | Recommended |
| --- | --- | --- |
| Without Forge | 2 CPU cores, 2 GB of memory (for example a Raspberry Pi with 2 GB) | 4 cores, 4 GB |
| With Forge on | 4 cores, 4 GB (for example a Raspberry Pi 5 with 4 GB) | 4 or more cores and 8 GB, such as a Pi 5 or an x86 PC |

These sizes are worked out from the measured use below, with room for the operating system and
Docker; the gateway has not been run on a 2 GB or a 4 GB machine to try them. Without Forge the
stack measured under 200 MB plus the notification server, which is held to 128 MB, so 2 GB leaves
well over a gigabyte free. Forge adds up to about 740 MB while it plays a game (its limit is 1.5 GB
and 2 cores), which 4 GB holds with room; 8 GB and more cores leave room to play two games at once
(raise `FORGE_MEMORY_LIMIT` and `FORGE_CPUS` first, as below).

### Measured use

| Part | Measured | Where | Its limit in the stack files |
| --- | --- | --- | --- |
| Gateway | about 82 MB, after the whole end-to-end test suite; about 17 MB more while the daily printings copy is built (an x86 machine, 0.7.46) | GitHub's x86 build machines, 0.7.46, two runs | 256 MB |
| Research service (Mystic Forge) | about 91 MB after the same suite; about 115 MB in an earlier measurement | GitHub's x86 build machines; an x86 machine | 512 MB |
| Notification server (ntfy) | not measured | | 128 MB |
| Forge, one game | peak about 600 MB; about 740 MB | GitHub's build machines (x86 and arm64); a Raspberry Pi 5 | 1.5 GB and 2 cores |
| Forge, two games at once | peak about 970 to 1,050 MB | GitHub's build machines | the same |

The gateway and research figures are `docker stats` readings; the Forge figures are the peak of
its container's memory while it played.

## How long a test takes

Measured, one game at a time:

| What | Machine | Time |
| --- | --- | --- |
| Solo test without Forge, 300 games | x86 Xeon 2.8 GHz, 4 cores | 1.4 to 1.5 seconds per deck (5 recent precons) |
| One Forge game against three precons | x86 Xeon 2.8 GHz, 2 cores | 29 seconds to 2 minutes 43 seconds (10 games, four precons) |
| The same seeded Forge game, alone | x86 Xeon 2.1 GHz, 4 cores | 18 to 19 seconds |
| The same seeded Forge game, alone | Raspberry Pi 5, 8 GB, running the rest of the stack | 38 seconds |
| One Forge game of the solo test, to a win, including Java's start | x86 Xeon 2.8 GHz | 15 to 19 seconds on 4 cores, 19 to 26 on 2 |

A Raspberry Pi 4 has not been timed, and the solo test has not been timed on any Pi. The solo-test
games were one of Forge's own Commander precons, which won on its fifth or sixth turn. Times vary
a lot with the decks: a deck that wins fast finishes fast.

## Making it faster

More CPU cores and memory for Forge is what helps. Forge plays one game per 2 cores and about
1.2 GB of memory at once:

- `FORGE_CPUS` and `FORGE_MEMORY_LIMIT` give Forge more of the machine (defaults 2 and 1536M).
  Forge works out how many games it can play at once from these.
- **Forge games per report** on the admin Settings page sets how many games a report plays
  (default 5). Fewer games finish sooner; more games give steadier numbers.
- A game that runs longer than four minutes is stopped and counted as a draw.

Every variable is in [DEPLOY.md](DEPLOY.md#environment-reference). Start from the defaults and
raise them while the machine keeps up.
