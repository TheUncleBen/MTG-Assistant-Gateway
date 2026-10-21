# What hardware you need

The gateway runs on any 64-bit ARM or x86 machine with Docker, a Raspberry Pi 4 or 5 included.
Forge, the optional game engine, is the only part that needs real power.

## Minimum and recommended

| | Minimum | Recommended |
| --- | --- | --- |
| Without Forge | 2 CPU cores, 2 GB of memory (a Raspberry Pi 4 with 2 GB) | 4 cores, 4 GB |
| With Forge on | 4 cores, 4 GB (a Raspberry Pi 4 or 5 with 4 GB) | 4 or more cores and 8 GB, on a Pi 5 or an x86 PC |

These are estimates from the memory limits the stack files set, not tested limits: the gateway
256 MB, the research service 512 MB, the notification server 128 MB, and Forge 1.5 GB and 2 CPU
cores, plus room for the operating system. Measured use is lower: the research service used about
115 MB on an x86 machine, and one Forge game peaked at about 600 MB on GitHub's build machines and
750 MB on a Pi 5.

## How long a test takes

Measured, one game at a time unless the row says otherwise:

| What | x86 PC | Raspberry Pi 5 |
| --- | --- | --- |
| Solo test without Forge, 300 games | 1.3 to 1.7 seconds (5 recent precons) | not measured yet |
| One Forge game against three precons, on 2 cores | 29 seconds to 4.5 minutes | about twice the x86 time (one game measured: 38 seconds against 18 to 19) |
| One Forge game of the solo test, to a win | 12 to 15 seconds on 4 cores, 15 to 21 seconds on 2 | not measured yet; expect about twice the x86 time |

The x86 figures come from a 4-core Xeon container (2.8 GHz), the Pi figure from a Pi 5 with 8 GB
running the rest of the stack. A Raspberry Pi 4 has not been timed. The solo-test games were
Forge's own starter Commander decks, which won on their fifth or sixth turn. Times vary a lot with
the decks: a deck that wins fast finishes fast.

## Making it faster

More CPU cores and memory for Forge is what helps. Forge plays one game per 2 cores and about
1.2 GB of memory at once:

- `FORGE_CPUS` and `FORGE_MEMORY_LIMIT` give Forge more of the machine (defaults 2 and 1536M).
  Forge works out how many games it can play at once from these.
- `MTG_FORGE_GAMES` sets how many games a report plays (default 4). Fewer games finish sooner;
  more games give steadier numbers.
- `FORGE_GAME_SECONDS` stops a game that runs too long and counts it as a draw (default 240).

Every variable is in [DEPLOY.md](DEPLOY.md#environment-reference). What suits your machine is
your call; start from the defaults and raise them while the machine keeps up.
