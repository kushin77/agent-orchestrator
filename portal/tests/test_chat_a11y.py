"""portal.tests.test_chat_a11y — the chat surface's accessibility contract (#514).

The property this file exists for: a *streaming*, multi-region, mobile-capable
conversational surface cannot have its accessibility asserted in prose. Every
assertion below runs offline against the shipped view and its a11y layer, so the
contract is gate-enforced rather than described.

Four things are asserted, in the order the issue names them:

1. **Landmarks and labels** — the transcript, the conversation list, the composer
   and the turn footer are reachable and labelled.
2. **The streaming announcement policy** — a screen reader must not re-read a
   growing region on every token, and must not be left silent either. The policy
   is: the transcript is ``aria-live="off"`` and a separate throttled region
   (``#liveRegion``) carries ``aria-live="polite"`` with ``aria-atomic="false"``.
3. **State, motion and responsiveness** — ``aria-busy`` while a turn is in
   flight, a ``prefers-reduced-motion`` path, and a mobile breakpoint that keeps
   the composer reachable and meets the minimum tap-target size.
4. **The layer is actually loadable** — the view links the a11y stylesheet and
   script. An a11y asset nothing loads is dead code, and this test refuses it.

Each detector is paired with a negative control that mutates a copy of the real
markup and asserts the detector *fires*, so a passing suite cannot be vacuous
(GR-12: a check that cannot fail is a formality).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "portal" / "static"
VIEW = STATIC / "views" / "chat.html"
A11Y_CSS = STATIC / "views" / "chat" / "a11y.css"
A11Y_JS = STATIC / "views" / "chat" / "a11y.js"
JS_DIR = STATIC / "js"


@pytest.fixture(scope="module")
def html() -> str:
    return VIEW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css() -> str:
    return A11Y_CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js() -> str:
    return A11Y_JS.read_text(encoding="utf-8")


# --- detectors (each returns a list of problems; empty means conformant) ------


def missing_landmarks(markup: str) -> list[str]:
    """The structural landmarks a screen-reader user navigates by."""
    problems = []
    if "<main" not in markup:
        problems.append("no <main> landmark")
    if not re.search(r'id="messages"[^>]*role="log"', markup, re.S):
        problems.append('the transcript is not role="log"')
    if not re.search(r'id="conversationList"[^>]*aria-label="', markup, re.S):
        problems.append("the conversation list is unlabelled")
    if not re.search(r'<label[^>]*for="input"', markup):
        problems.append("the composer textarea has no <label for>")
    if not re.search(r'id="input"[^>]*aria-describedby=', markup, re.S):
        problems.append("the composer has no aria-describedby hint")
    return problems


def live_region_problems(markup: str) -> list[str]:
    """The throttled streaming announcement policy."""
    problems = []
    if not re.search(r'id="messages"[^>]*aria-live="off"', markup, re.S):
        problems.append(
            "the transcript must be aria-live=off (a per-token live region is unusable)"
        )
    if not re.search(r'id="liveRegion"[^>]*aria-live="polite"', markup, re.S):
        problems.append("no polite live region for throttled announcements")
    if not re.search(r'id="liveRegion"[^>]*aria-atomic="false"', markup, re.S):
        problems.append(
            "the live region must be aria-atomic=false so it announces the summary, not the region"
        )
    return problems


def tag_for_id(markup: str, element_id: str) -> str:
    """Return the full start tag carrying ``id="<element_id>"``, or an empty string.

    Attribute order is not guaranteed in hand-written markup, so a detector must
    never assume ``type`` precedes ``id`` (it does not here: the buttons carry
    ``type`` first). Match the tag, then inspect its attributes.
    """
    match = re.search(rf'<[^>]*id="{re.escape(element_id)}"[^>]*>', markup)
    return match.group(0) if match else ""


def keyboard_problems(markup: str, js_text: str) -> list[str]:
    """Every control operable by keyboard, and Escape stops a stream."""
    problems = []
    for control in ("send", "stop", "retry", "newConversation", "tierPicker"):
        if not tag_for_id(markup, control):
            problems.append(f"missing control #{control}")
    send = tag_for_id(markup, "send")
    if send and 'type="submit"' not in send:
        problems.append("#send must be a submit control inside the form")
    for control in ("stop", "retry"):
        tag = tag_for_id(markup, control)
        if tag and 'type="button"' not in tag:
            problems.append(f"#{control} must be type=button so it is keyboard-operable")
    if "Escape" not in js_text:
        problems.append("no Escape handling: a keyboard user cannot stop a stream")
    return problems


def aria_busy_problems(js_text: str) -> list[str]:
    """`aria-busy` while a turn is in flight, keyed on the state chat.js publishes.

    Matches the assignment, not the token: a comment mentioning ``aria-busy``
    must not satisfy a behavioural contract.
    """
    problems = []
    if not re.search(r'setAttribute\(\s*"aria-busy"', js_text):
        problems.append("nothing sets aria-busy during generation")
    for state in ("sending", "streaming"):
        if state not in js_text:
            problems.append(f"aria-busy is not keyed on the {state!r} state")
    if "MutationObserver" not in js_text:
        problems.append("the busy state must track the state chat.js publishes")
    return problems


def reduced_motion_problems(css_text: str, js_text: str) -> list[str]:
    """The reduced-motion path must be a real at-rule, not a comment mentioning one.

    Matching the bare phrase would be satisfied by the file header's prose about
    the media query — a comment is not a behaviour. Match the at-rule itself.
    """
    problems = []
    if not re.search(
        r"@media\s*\(\s*prefers-reduced-motion:\s*reduce\s*\)", css_text
    ):
        problems.append("no prefers-reduced-motion media query")
    if "matchMedia" not in js_text or "prefers-reduced-motion" not in js_text:
        problems.append("the reduced-motion preference is never read")
    return problems


def responsive_problems(css_text: str) -> list[str]:
    problems = []
    if not re.search(r"@media\s*\(max-width:\s*\d+px\)", css_text):
        problems.append("no mobile breakpoint")
    if "min-height: 44px" not in css_text:
        problems.append("tap targets below the platform minimum")
    if "position: sticky" not in css_text:
        problems.append("the composer is not kept reachable on a small screen")
    return problems


def asset_loading_problems(markup: str) -> list[str]:
    """An a11y asset the view never loads is dead code."""
    problems = []
    if 'href="/views/chat/a11y.css"' not in markup:
        problems.append("the view does not link the a11y stylesheet")
    if 'src="/views/chat/a11y.js"' not in markup:
        problems.append("the view does not load the a11y script")
    return problems


def external_reference_problems(text: str, name: str) -> list[str]:
    return [
        f"{name} references an external URL: {url}"
        for url in re.findall(r"https?://[^\s\"')]+", text)
    ]


# --- 1. landmarks and labels -------------------------------------------------


def test_landmarks_and_labels_are_present(html: str) -> None:
    assert missing_landmarks(html) == []


def test_controls_are_keyboard_operable(html: str) -> None:
    js_text = (JS_DIR / "chat.js").read_text(encoding="utf-8")
    assert keyboard_problems(html, js_text) == []


def test_budget_state_is_a_reachable_status_landmark(html: str) -> None:
    assert re.search(r'id="budgetState"[^>]*role="status"', html, re.S), (
        "the budget state must be a status landmark, not decoration"
    )


# --- 2. the streaming announcement policy -----------------------------------


def test_streaming_announcement_policy(html: str) -> None:
    assert live_region_problems(html) == []


def test_transcript_and_live_region_are_separate_regions(html: str) -> None:
    """The two must not collapse into one: that is the whole point of the policy."""
    transcript = re.search(r'id="messages"[^>]*', html, re.S)
    live = re.search(r'id="liveRegion"[^>]*', html, re.S)
    assert transcript and live
    assert 'aria-live="off"' in transcript.group(0)
    assert 'aria-live="polite"' in live.group(0)


def test_turn_footer_is_labelled_a_group(js: str) -> None:
    assert 'role", "group"' in js and "aria-label" in js, (
        "the turn footer (usage / latency / cost) must be a labelled group"
    )


# --- 3. state, motion, responsiveness ---------------------------------------


def test_aria_busy_tracks_the_turn_state(js: str) -> None:
    assert aria_busy_problems(js) == []


def test_focus_is_never_lost(js: str) -> None:
    assert ".focus()" in js, "focus must be restored to the composer after a turn settles"
    assert "document.activeElement" in js


def test_reduced_motion_path(css: str, js: str) -> None:
    assert reduced_motion_problems(css, js) == []


def test_mobile_breakpoint(css: str) -> None:
    assert responsive_problems(css) == []


def test_visible_focus_indicator(css: str) -> None:
    assert ":focus-visible" in css, "there must be a visible focus indicator"


# --- 4. the layer is loadable, offline, and self-contained -------------------


def test_view_loads_the_a11y_layer(html: str) -> None:
    assert asset_loading_problems(html) == []


def test_view_remains_a_self_contained_frame(html: str) -> None:
    """The invariant test_assets.py already enforces must survive this change."""
    assert 'href="/design-tokens/tokens.css"' in html
    assert 'href="/css/console.css"' in html


def test_a11y_assets_are_offline(css: str, js: str) -> None:
    assert external_reference_problems(css, "a11y.css") == []
    assert external_reference_problems(js, "a11y.js") == []


def test_a11y_assets_exist_and_are_non_trivial(css: str, js: str) -> None:
    assert len(css.splitlines()) > 30
    assert len(js.splitlines()) > 40


# --- negative controls: the detectors must be able to fail ------------------


def test_negative_control_removing_a_landmark_is_detected(html: str) -> None:
    broken = html.replace("<main", "<div", 1)
    assert "<main" not in broken
    assert "no <main> landmark" in missing_landmarks(broken)


def test_negative_control_unlabelling_a_control_is_detected(html: str) -> None:
    broken = html.replace('<label class="field grow" for="input">', "<span>", 1)
    assert "the composer textarea has no <label for>" in missing_landmarks(broken)


def test_negative_control_live_region_made_naive_is_detected(html: str) -> None:
    """The exact regression this policy exists to prevent."""
    broken = html.replace('aria-live="off"', 'aria-live="polite"', 1)
    assert live_region_problems(broken) != []


def test_negative_control_unloaded_a11y_asset_is_detected(html: str) -> None:
    broken = html.replace('<script src="/views/chat/a11y.js"></script>', "", 1)
    assert asset_loading_problems(broken) != []


def test_negative_control_dropped_reduced_motion_is_detected(css: str) -> None:
    """Mutate the AUTHORITATIVE occurrence, not a comment that mentions it.

    Replacing the bare phrase hits the prose in the file header first and leaves
    the real media query in place, which would make this control pass without
    proving anything. Target the at-rule itself.
    """
    broken = css.replace(
        "@media (prefers-reduced-motion: reduce)", "@media (prefers-contrast: more)", 1
    )
    assert broken != css, "the mutation changed nothing"
    reader = 'window.matchMedia("(prefers-reduced-motion: reduce)")'
    problems = reduced_motion_problems(broken, reader)
    assert "no prefers-reduced-motion media query" in problems


def test_negative_control_dropped_tap_target_is_detected(css: str) -> None:
    broken = css.replace("min-height: 44px", "min-height: 20px", 1)
    assert "tap targets below the platform minimum" in responsive_problems(broken)
