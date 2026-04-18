"""B18 — unit tests for the homepage `+ New project` selector list in
`flow/operations/generate.py`.

Pre-B18 the selector list was EN-only (`button:has-text('New project')`).
On a VI-locale Google account the Flow homepage renders `Dự án mới`, so
every `text-to-video` job on a non-EN profile raised
`RuntimeError("Failed to find '+ New project' button on Flow homepage")`
at `generate.py:125` without even reaching the B1 aspect-ratio code.

Post-B18 the selector list is locale-independent. Live DOM probe
(2026-04-18 on ngoctuandt20 VI profile) captured the ground truth:

    <button>
      <i class="google-symbols">add_2</i>  ← Material Icon ligature (stable)
      Dự án mới | New project                ← localized (unstable)
      <div data-type="button-overlay"/>
    </button>

The only stable locale-independent signal is the Material Icon ligature
text `add_2` inside the `<i class="google-symbols">` child. `aria-label`
is EMPTY, `href` is EMPTY (not an anchor), no `role` / `id` / `data-testid`.

These tests guard the selector list so a future refactor cannot silently
reintroduce an EN-only list. Full behavioural (Playwright) coverage is
out of scope — Tier 2 live E2E exercises the real path.
"""

import re

from flow.operations import generate


# ---------------------------------------------------------------------------
# Module-level contract — the selector list is exported for reuse (re-click
# loop after login recovery references the same list).
# ---------------------------------------------------------------------------


def test_new_project_selectors_is_list_of_strings():
    """NEW_PROJECT_SELECTORS is a non-empty list of CSS/Playwright selectors."""
    assert isinstance(generate.NEW_PROJECT_SELECTORS, list)
    assert len(generate.NEW_PROJECT_SELECTORS) > 0
    for sel in generate.NEW_PROJECT_SELECTORS:
        assert isinstance(sel, str) and sel.strip(), f"bad selector {sel!r}"


def test_icon_selector_comes_first():
    """B18 contract: the icon-based (locale-independent) selector is probed
    BEFORE any text-based variant. Text-based probing first means a VI
    profile would burn time on EN selectors that cannot match — keeping
    the icon selector at the top short-circuits on the first iteration.

    Regression guard: if a refactor accidentally reorders text-first,
    this test fails.
    """
    first = generate.NEW_PROJECT_SELECTORS[0]
    assert "add_2" in first, (
        f"First selector must probe the Material Icon ligature 'add_2' "
        f"(locale-independent), got {first!r}"
    )
    # First-three should all be icon-based for fast-path on every locale.
    top_three = generate.NEW_PROJECT_SELECTORS[:3]
    for sel in top_three:
        assert "add_2" in sel, (
            f"Top-3 selectors must be icon-first (contain 'add_2'), got {sel!r}"
        )


def test_bilingual_text_fallbacks_present():
    """Defense in depth: both VI and EN text variants MUST be probed so
    the engine survives a locale flip or a Flow DOM change that removes
    the icon ligature. The pre-B18 list had EN only — this trip-wire
    prevents silent drift back to that state.
    """
    all_selectors = " | ".join(generate.NEW_PROJECT_SELECTORS)
    # Vietnamese (primary non-EN locale in FlowEngine accounts)
    assert "Dự án mới" in all_selectors, (
        "Selector list must include Vietnamese 'Dự án mới' fallback"
    )
    # English (canonical fallback)
    assert "New project" in all_selectors, (
        "Selector list must include English 'New project' fallback"
    )


def test_icon_selector_uses_google_symbols_class():
    """The live-DOM probe found the icon ligature inside
    `<i class="google-symbols">`. At least one selector should combine
    both the class and the ligature text — the most specific form.
    """
    has_compound = any(
        "google-symbols" in sel and "add_2" in sel
        for sel in generate.NEW_PROJECT_SELECTORS
    )
    assert has_compound, (
        "At least one selector should compound the google-symbols class "
        "AND the add_2 ligature (most specific icon match)"
    )


def test_generic_create_selectors_are_last():
    """'Create' / 'Tạo' match too broadly — they can hit welcome overlay
    buttons or unrelated CTAs. They are the last-resort fallbacks and
    must be kept below the icon + text-variant selectors.
    """
    selectors = generate.NEW_PROJECT_SELECTORS

    def _pos(substr):
        return next(
            (i for i, s in enumerate(selectors) if substr in s and "add_2" not in s),
            -1,
        )

    create_idx = _pos("'Create'")
    tao_idx = _pos("'Tạo'")
    icon_first_idx = next(
        (i for i, s in enumerate(selectors) if "add_2" in s), -1
    )
    du_an_idx = next(
        (i for i, s in enumerate(selectors) if "Dự án mới" in s), -1
    )

    assert icon_first_idx != -1, "icon selector must exist"
    assert du_an_idx != -1, "Vietnamese selector must exist"

    # Icon selector runs first; 'Create' / 'Tạo' come after icon AND after
    # the specific bilingual-text variants.
    if create_idx != -1:
        assert create_idx > icon_first_idx
        assert create_idx > du_an_idx, (
            "'Create' must appear after 'Dự án mới' — the specific text "
            "fallback takes priority over the generic one"
        )
    if tao_idx != -1:
        assert tao_idx > icon_first_idx
        assert tao_idx > du_an_idx


def test_selector_list_is_shared_with_retry_path():
    """Post-B18 refactor: the list must be module-level so the retry
    loop (after login recovery in `text_to_video`) reuses exactly the
    same selector ordering. A pre-B18 bug would see the retry branch
    diverge silently from the primary branch.
    """
    import inspect

    source = inspect.getsource(generate.text_to_video)
    # The function body must reference the module-level constant by name
    # in both the primary click loop AND the post-login re-click loop.
    occurrences = source.count("NEW_PROJECT_SELECTORS")
    assert occurrences >= 2, (
        f"text_to_video must reference NEW_PROJECT_SELECTORS in BOTH the "
        f"primary click loop and the post-login retry loop (expected >= 2, "
        f"got {occurrences})"
    )


# ---------------------------------------------------------------------------
# Source-level trip-wires (prevent silent regression)
# ---------------------------------------------------------------------------


def test_source_does_not_reintroduce_en_only_list():
    """Trip-wire: the pre-B18 failure was an EN-only list. If a refactor
    removes the Vietnamese entries we want the test suite to yell.
    """
    joined = "\n".join(generate.NEW_PROJECT_SELECTORS)
    # Must cover at least the homepage VI label AND the icon ligature.
    assert re.search(r"Dự án mới", joined), (
        "Selector list lost the 'Dự án mới' entry — VI locale is blocked"
    )
    assert re.search(r"add_2", joined), (
        "Selector list lost the 'add_2' icon ligature — locale-independence lost"
    )
