"""The public docs describe the gateway as it is now: the approval modes by their real labels, no
promise the modes break, every update step an operator needs, the deploy guides without version
history, and hardware sizes from measured figures."""

from __future__ import annotations

import re
from pathlib import Path

from mtg_gateway import modes

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md"]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_every_mode_shown_as_in_the_docs_is_its_real_label() -> None:
    labels = set(modes.MODE_LABELS.values())
    found = 0
    for path in DOCS:
        text = " ".join(path.read_text(encoding="utf-8").split())  # a label may wrap across lines
        for quoted in re.findall(r'shown as "([^"]+)"', text):
            found += 1
            assert quoted in labels, (path.name, quoted)
        for quoted in re.findall(r'"(Apply [^"]*without asking)"', text):
            assert quoted in labels, (path.name, quoted)
    assert found >= 6


def test_the_readme_promise_holds_in_every_mode() -> None:
    """Semi-auto and Full auto let an assistant apply without a press, so the README can't say a
    deck never changes before the member says yes."""
    head = _read("README.md").split("## Contents")[0]
    flat = " ".join(head.split())
    assert "never changes a deck until" not in flat
    assert "only the way that person chose" in flat and "out of the box, only after they say yes" in flat


def test_upgrading_has_the_0_7_46_steps_that_reach_past_the_stack() -> None:
    text = _read("docs/UPGRADING.md")
    section = text.split("## Coming from an older version")[1].split("## Older update notes")[0]
    coming = " ".join(section.split())
    for step in (
        "Update the Android app",
        "Never sleeping apps",
        "paste the instructions block from the gateway's ChatGPT setup page again",
        "`propose_account_action`",
        "`propose_deck_social`",
        "`propose_contest_entry`",
    ):
        assert step in coming, step


def test_the_deploy_guides_describe_the_current_version_only() -> None:
    history = re.compile(
        r"\b(?:[Bb]efore|[Ss]ince|[Ff]rom|[Uu]ntil|[Aa]s of|update to|[Aa]fter) "
        r"(?:version )?0\.\d+(?:\.\d+)?\b"
    )
    for name in ("docs/DEPLOY.md", "docs/DEPLOY-COMPOSE.md"):
        text = " ".join(_read(name).split())
        assert not history.findall(text), (name, history.findall(text))
        assert "(0.7.30 among them)" not in text


def test_hardware_sizes_come_from_measured_figures_and_say_what_is_not_measured() -> None:
    text = _read("docs/HARDWARE.md")
    flat = " ".join(text.split())
    assert "These are estimates" not in flat and "not tested limits" not in flat
    assert "### Measured use" in text
    measured = text.split("### Measured use")[1].split("\n## ")[0]
    for part in ("Gateway", "Research service", "Notification server", "Forge, one game"):
        assert f"| {part}" in measured, part
    # what was not measured says so, and the sizes say how they were reached
    assert "| not measured |" in measured
    assert "worked out from the measured use below" in flat
    assert "has not been run on a 2 GB or a 4 GB machine" in flat
    # the other docs' memory figures agree with it
    for name in ("docs/DEPLOY.md", "docs/ARCHITECTURE.md"):
        doc = " ".join(_read(name).split())
        assert "about 60 MB" not in doc and "idles around 60 MB" not in doc, name
        assert "about 82 MB" in doc, name
