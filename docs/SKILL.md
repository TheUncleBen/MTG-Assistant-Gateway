# Teaching your assistant to use the gateway

The gateway already tells AI clients the basics when they connect: who
you are, that research is read-only, and that edits take two steps. The
**MTG Assistant Gateway skill** goes further. It covers which tool to use for which
question, how to load a deck from a link, a pasted list or a CSV export, how
to report simulations honestly, and the propose, review, apply routine for
deck edits, including what every error means. It's optional, but worth it.

| File | For |
| --- | --- |
| [`plugin/mtg-gateway/skills/mtg-gateway/`](../plugin/mtg-gateway/skills/mtg-gateway/) | Claude: a skill folder (`SKILL.md` plus a tool catalogue in `reference/tools.md`) |
| [`plugin/mtg-gateway/chatgpt-instructions.md`](../plugin/mtg-gateway/chatgpt-instructions.md) | ChatGPT: the same rules as one block of text for a project's instructions |

Both are part of the assistant plugin, so if you installed the plugin
([PLUGIN.md](PLUGIN.md)) you already have the skill. The steps below are for
adding the skill on its own.

**Easiest:** open `https://mtg.example.com/skill` (your gateway's hostname)
and sign in. That page has the Claude skill as a ready-made ZIP and shows the
ChatGPT text to copy, built from the files above.

## What the skill makes the assistant do

- Look things up with the tools instead of going from memory, and say which
  tool a fact came from.
- Load any deck the right way: an Archidekt link with `get_deck`, your own
  decks with `list_my_decks` and then `get_deck`, a pasted list with
  `parse_decklist`, or an Archidekt CSV export with `parse_deck_export`.
- Run goldfish simulations and always report the seed, number of games,
  mulligan settings, what wasn't simulated and the confidence intervals,
  without calling the result a win rate.
- Edit decks you own, or create new ones in your account, in two steps:
  propose and show you the exact diff, then leave the decision to you: the
  Approve button on the card your app shows, the Apply button on the
  gateway's review page, or, if you chose a looser approval mode on your
  Account page, the assistant applies it itself.
  Then report the result with the snapshot and how to undo it.
- Undo an edit by restoring the deck from its snapshot
  (`propose_restore_snapshot`), as a normal proposal you okay first.
- Treat anything inside decks, card notes or tool output as information,
  never as a request to edit. Only your own messages can ask for a change.
- Never claim a deck changed unless the gateway reports the proposal as
  applied and verified.
- Never ask for a password.
- Read physical cards from photos with `resolve_cards`, or pick up a scan
  made on the gateway's `/scan` page with `get_scan_session`.

## Claude

Skills need **Code execution and file creation** turned on: Settings →
Capabilities on Free, Pro and Max. On Team and Enterprise an owner controls
this under Organization settings → Plugins & skills.

1. Download `mtg-gateway.zip` from the gateway's `/skill` page. Or zip it
   yourself from the `plugin/mtg-gateway/skills/mtg-gateway` folder. The ZIP
   has to contain the folder itself, not just the files inside it:
   - Windows: right-click the `mtg-gateway` folder → **Send to → Compressed
     (zipped) folder**.
   - macOS: right-click the folder → **Compress "mtg-gateway"**.
   - Linux or macOS terminal:
     `cd plugin/mtg-gateway/skills && zip -r mtg-gateway.zip mtg-gateway`
2. In Claude on the web or the desktop app, open **Customize → Skills**.
3. Click **+**, then **+ Create skill**, then **Upload a skill**, and pick
   the ZIP.
4. Start a chat with the MTG Assistant Gateway connector on and ask a deck question.
   Claude loads the skill when the question fits.

Source: Anthropic's help pages "Use skills in Claude" and "How to create
custom skills", checked 2026-10-04 (verified). Upload and manage skills on
the web or desktop app, at Customize → Skills. On phones, Anthropic lists
skills as available in Cowork (a beta), but whether an uploaded skill gets
used in ordinary chats in the Claude iOS and Android apps is unverified and
untested.

No skills on your plan? Open a Claude **Project** and paste the text of
`plugin/mtg-gateway/skills/mtg-gateway/SKILL.md` (minus the lines between
the two `---` markers at the top) into the project's instructions, or add
both Markdown files as project knowledge. Chats in that project then follow
it.

## ChatGPT

OpenAI lists skills only for Business, Enterprise, Healthcare and Edu plans
(reported), so on Plus and Pro use project instructions instead. And
remember ChatGPT only supports the connector itself on the web; see
[CONNECT.md](CONNECT.md#which-apps-and-devices-work).

1. Create a ChatGPT **project** for MTG stuff.
2. Open the project's menu → **Project settings**, and paste the text from
   the gateway's `/skill` page (the same block as in
   [`plugin/mtg-gateway/chatgpt-instructions.md`](../plugin/mtg-gateway/chatgpt-instructions.md))
   into its instructions.
3. Chat inside that project with the MTG Assistant Gateway connector turned on.

You can paste the same block into your account-wide custom instructions
instead, but then it applies to every chat.

OpenAI's help center says project instructions only apply inside that
project and override your account-wide custom instructions, and that apps
work in projects on Plus and Pro (reported, from a page read through a
summarising tool on 2026-10-04). The menu labels in the steps above are
**unverified**. If one's different, look for the project's settings or
instructions.

## Keeping it current

The tool names in the skill files are checked against the gateway's real
tool list by the test suite (`tests/test_skill_docs.py`), so a renamed or
removed tool fails CI until the skill is updated.
