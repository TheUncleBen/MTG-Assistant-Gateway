"""Every kind of problem the owner has reported, as a permanent rule with the checks that hold it.

A reported problem is fixed as a CLASS, not as the one place it was seen: the fix goes everywhere the
same kind of problem can appear, and a check is added that fails if it ever comes back anywhere
(written first, failing on the code before the fix). This list is the memory of that: one rule per
kind of problem, in plain words, with the checks that hold it. tests/test_issue_rules.py fails if a
check named here no longer exists, so a rule cannot quietly lose its check.

A rule a script cannot judge (a word that reads wrong, a feature in a place that does not make
sense) says so in ``human``: those are what a person still looks at, and nothing else is.

To add a rule: a new ``Rule`` with the next id, the problem in the owner's words, and the checks
(``tests/file.py::test_name``, or ``tool:`` for a step of the review loop that is not a pytest).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rule:
    id: str
    problem: str
    checks: tuple[str, ...] = ()
    human: str = ""  # what of it only a person can judge
    covers: tuple[str, ...] = field(default=())  # where it applies: every page, every screen, ...


RULES: list[Rule] = [
    Rule(
        "IR-01",
        "A page scrolls sideways, or a control is cut off at the edge, on some screen",
        (
            "tests/test_overflow_sweep_browser.py::test_no_page_scrolls_sideways_or_clips_a_control",
            "tests/test_ui_matrix_browser.py::test_every_page_meets_the_ui_standard",
        ),
        covers=("every page", "phone, Fold, tablet, laptop, desktop", "hostile long names"),
    ),
    Rule(
        "IR-02",
        "Words broken in the middle, or headings hyphenated",
        (
            "tests/test_text_scales.py::test_no_stylesheet_hyphenates",
            "tests/test_text_scales.py::test_no_stylesheet_breaks_every_word",
            "tests/test_text_zoom_browser.py::test_names_and_headings_keep_whole_words_at_200_percent_on_narrow_phones",
        ),
        covers=("every stylesheet", "names and headings at 200% text"),
    ),
    Rule(
        "IR-03",
        "A button, link or menu item that does nothing when pressed",
        ("tests/test_ui_clicks_browser.py::test_no_control_is_dead_or_broken",),
        human="whether what it does is what its words promise",
        covers=("every control on every page and in every menu and box it opens",),
    ),
    Rule(
        "IR-04",
        "A task does the wrong thing: a card added twice, saved to the wrong place, or shown but not saved",
        (
            "tests/test_tasks_browser.py::test_add_a_card_to_a_category",
            "tests/test_tasks_browser.py::test_change_copies_then_remove_a_card",
            "tests/test_tasks_browser.py::test_move_a_card_to_another_category",
            "tests/test_tasks_browser.py::test_read_and_clear_notifications",
            "tests/test_tasks_browser.py::test_change_a_wants_copies_and_note",
        ),
        covers=("phone, Fold, laptop", "the store, the page after a reload, and a second page"),
    ),
    Rule(
        "IR-05",
        "Pages that look like different apps: a part (title, heading, button, chip, panel, box, menu) "
        "drawn its own way on one page",
        (
            "tests/test_ui_cohesion_browser.py::test_every_page_draws_each_role_in_the_standards_looks",
            "tests/test_ui_layers_browser.py::test_every_box_and_menu_has_the_one_look",
            "tests/test_shared_parts.py::test_no_file_draws_more_of_its_own_parts_than_before",
        ),
        human="taste: whether the one look is the right look",
        covers=("every page", "every box and menu a page opens", "every page's stylesheet and script"),
    ),
    Rule(
        "IR-06",
        "A change made in one place and left behind in the others (the website changed, the app, the "
        "cards, the tools or the docs not)",
        (
            "tests/test_impact_map.py::test_a_place_the_map_lists_and_the_change_left_is_a_miss",
            "tool:impact_map check",
        ),
        human="the impact map itself: that every place the request reaches is on it",
        covers=("web, Android, Claude and ChatGPT cards, assistant tools, plugin and skill, docs",),
    ),
    Rule(
        "IR-07",
        "A change reaches a page it should not have, or does more than was asked",
        (
            "tests/test_impact_map.py::test_a_file_the_map_does_not_list_is_scope_creep",
            "tool:shot_diff run",
        ),
        human="reviewing the pictures of the pages that were expected to change",
        covers=("every page on phone, Fold and laptop",),
    ),
    Rule(
        "IR-08",
        "Text too faint to read, in light or dark",
        (
            "tests/test_text_contrast.py::test_site_text_tokens_pass_in_light_and_dark",
            "tests/test_text_contrast.py::test_words_on_pictures_and_picture_stand_ins_pass",
        ),
        covers=("every colour token, light and dark",),
    ),
    Rule(
        "IR-09",
        "Pages fall apart with large text (200%)",
        ("tests/test_text_zoom_browser.py::test_main_pages_hold_together_at_200_percent_text",),
        covers=("the main pages at 200% text",),
    ),
    Rule(
        "IR-10",
        "A filter sheet's foot covers its last control",
        ("tests/test_ui_matrix_browser.py::test_a_filter_sheets_foot_never_covers_its_last_control",),
        covers=("every filter sheet",),
    ),
    Rule(
        "IR-11",
        "A feature in a place that does not make sense (Card models under More instead of Account)",
        ("tests/test_shell_polish.py::test_card_models_are_a_row_on_the_account_page_not_in_more",),
        human="where a new feature belongs: put to the owner when no existing pattern decides it",
        covers=("the one placement the owner set",),
    ),
    Rule(
        "IR-12",
        "A control that is there but has no purpose (the Sign-in line on This app)",
        ("tests/test_ui_clicks_browser.py::test_no_control_is_dead_or_broken",),
        human="whether a control that does something is worth having",
        covers=("controls that do nothing; a purposeless one that does something is a person's call",),
    ),
    Rule(
        "IR-13",
        "A control smaller than a finger (under 44 px) on a touch screen",
        ("tests/test_ui_matrix_browser.py::test_every_page_meets_the_ui_standard",),
        covers=("every page on every touch screen",),
    ),
]
