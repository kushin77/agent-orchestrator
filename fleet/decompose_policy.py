#!/usr/bin/env python3
"""The micro-decomposition POLICY — sizing, anti-explosion guards, wave cap.

Epic #707 lane F4 / issue #719.

`fleet/brain.py` could already *file* a decomposition, but it validated the
order's SHAPE only (`decompose_problem`): a "child" naming three lanes and five
`Verify:` lines went straight to `gh issue create`, a second child that merely
re-stated an issue already open on the board was filed as a duplicate, and a
wave of two hundred children filed just as happily as a wave of two. The shape
was right and the *sizing* was unchecked, which is exactly how a decomposition
explodes.

This module is the missing policy. It is PURE and OFFLINE on purpose — no
network, no clock, no filesystem — so the rule can be *proved* in a unit test
instead of being asserted in a docstring:

* :func:`sizing_problem` — is ONE child a MICRO child?
* :func:`duplicate_problem` — is this child already on the open board?
* :func:`wave_problems` — the wave validator: sizing + duplicates + cap.
* :func:`effective_cap` — ``FLEET_DECOMPOSE_CAP``, else the focus ``wave_cap``,
  else :data:`DEFAULT_CAP`.

The caller (the brain) supplies both the candidate children and the OPEN issues
they are compared against. That is what keeps the duplicate guard honest: it
never consults the network, so the verdict does not change with whichever board
happened to be reachable at the moment.

The sizing rule (a child is MICRO when it has) — epic #707:

  exactly 1 lane + exactly 1 runnable ``Verify:`` line + exactly 1 acceptance
  criterion + a non-empty ``Files:`` set, and ``depth <= 2`` (epic -> micro-child).

A child that names *more* than one of those is RE-SPLITTABLE: it is not a
micro-child at all, it is a seam, and the refusal names the parent seam to
decompose instead — the rule terminates because a micro-child is never split.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable, Mapping, Sequence

#: The environment knob a wave's size can be pinned with.
CAP_ENV = "FLEET_DECOMPOSE_CAP"
#: The cap when neither the environment nor the board focus declares one. It is
#: `governance/dispatch/focus.py`'s `DEFAULT_WAVE_CAP` restated rather than
#: imported, so this module stays dependency-free; `self_control` fails when the
#: two drift apart, so the restatement cannot quietly diverge.
DEFAULT_CAP = 12
#: ``epic -> micro-child``. A child of a micro-child would be a third rung, and
#: the chain is bounded at two on purpose: it must terminate at "1 lane".
MAX_DEPTH = 2

#: Prefix every re-splittable refusal carries, so a caller (and a test) can tell
#: "this child is too big" from "this child is too small" without prose matching.
RESPLITTABLE = "is RE-SPLITTABLE"

#: The separator a `lane` string may use to smuggle in more than one lane.
_LANE_SPLIT = re.compile(r"[,;|]")


def _norm(text: object) -> str:
    """Case/whitespace-insensitive key for identity comparisons."""
    return " ".join(str(text or "").split()).casefold()


def _sequence(value: Any) -> list[Any]:
    """A list from a list/tuple, from a string (not split), else ``[]``."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def lanes(child: Mapping[str, Any]) -> list[str]:
    """The lanes this child names, in declaration order, deduplicated.

    ``lane`` (one string) and ``lanes`` (a sequence) are both accepted; a string
    that carries a ``,``/``;``/``|`` is read as the several lanes it names, which
    is how a list sneaks into a single field in practice.
    """
    found: list[str] = []
    for key in ("lanes", "lane"):
        for raw in _sequence(child.get(key)):
            for part in _LANE_SPLIT.split(str(raw or "")):
                part = part.strip()
                if part:
                    found.append(part)
    unique: list[str] = []
    for lane in found:
        if lane not in unique:
            unique.append(lane)
    return unique


_VERIFY_PREFIX = "verify:"


def _verify_lines_from(text: str) -> list[str]:
    """The runnable commands in one ``verify`` blob (``Verify:`` prefix stripped)."""
    commands: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.casefold().startswith(_VERIFY_PREFIX):
            stripped = stripped[len(_VERIFY_PREFIX):].strip()
        if stripped:
            commands.append(stripped)
    return commands


def verify_lines(child: Mapping[str, Any]) -> list[str]:
    """The runnable ``Verify:`` commands this child names.

    A micro-child names exactly one. Both ``verify`` (a string, possibly with the
    ``Verify:`` label and newlines) and ``verifies`` (a sequence) are accepted.
    """
    found: list[str] = []
    for raw in _sequence(child.get("verifies")):
        found.extend(_verify_lines_from(str(raw or "")))
    for raw in _sequence(child.get("verify")):
        found.extend(_verify_lines_from(str(raw or "")))
    return found


def criteria(child: Mapping[str, Any]) -> list[str]:
    """The acceptance criteria this child declares (exactly one, for a micro-child)."""
    found: list[str] = []
    for key in ("criterion", "criteria"):
        for raw in _sequence(child.get(key)):
            text = str(raw or "").strip()
            if text:
                found.append(text)
    return found


def files(child: Mapping[str, Any]) -> list[str]:
    """The files this child owns; a non-empty set is what makes it collision-free."""
    found: list[str] = []
    for raw in _sequence(child.get("files")):
        for part in str(raw or "").replace(",", "\n").splitlines():
            text = part.strip()
            if text and text not in found:
                found.append(text)
    return found


def sizing_problem(child: Any, *, depth: int = 1) -> str | None:
    """Why ``child`` is not a MICRO child, or ``None`` when it is.

    The one place the sizing rule lives. Every refusal names the child (by title,
    so the operator can find it) and, for a child that is too *big*, names the
    parent seam to decompose instead (``RESPLITTABLE``).
    """
    if not isinstance(child, Mapping):
        return f"child is not an object (title/lane/verify/files): {type(child).__name__}"
    title = str(child.get("title", "") or "").strip()
    who = f"child '{title}'" if title else "child (untitled)"
    if not title:
        return f"{who}: names no title — a micro-child is filed by name"
    named_lanes = lanes(child)
    if not named_lanes:
        return f"{who}: names no lane — a micro-child owns exactly one lane"
    if len(named_lanes) > 1:
        return (
            f"{who} {RESPLITTABLE}: it names {len(named_lanes)} lanes "
            f"({', '.join(named_lanes)}) — decompose the parent SEAM instead "
            "(the chain must terminate at '1 lane')"
        )
    named_verifies = verify_lines(child)
    if not named_verifies:
        return f"{who}: names no runnable Verify: line — a micro-child has exactly one"
    if len(named_verifies) > 1:
        return (
            f"{who} {RESPLITTABLE}: it names {len(named_verifies)} runnable Verify: lines "
            f"({'; '.join(named_verifies)}) — decompose the parent SEAM instead "
            "(a micro-child has exactly one)"
        )
    named_criteria = criteria(child)
    if not named_criteria:
        return f"{who}: names no acceptance criterion — a micro-child has exactly one"
    if len(named_criteria) > 1:
        return (
            f"{who} {RESPLITTABLE}: it names {len(named_criteria)} acceptance criteria — "
            "decompose the parent SEAM instead (a micro-child has exactly one)"
        )
    if not files(child):
        return f"{who}: names no Files: — a micro-child owns a non-empty, disjoint file set"
    if depth > MAX_DEPTH:
        return (
            f"{who}: depth {depth} exceeds the bound of {MAX_DEPTH} (epic -> micro-child) — "
            "decompose the parent SEAM instead (the chain must terminate at '1 lane')"
        )
    return None


def _open_tuple(entry: Any) -> tuple[int | None, str, str, str]:
    """Normalise one open-issue entry to ``(number, title, lane, verify)``.

    A caller may pass the documented ``(number, title, lane)`` tuple, a longer
    tuple that also carries the ``Verify:`` line, or a mapping with those keys —
    a board snapshot gives the brain titles and no lane, a live board gives it
    everything, and neither shape should force the guard to change.
    """
    if isinstance(entry, Mapping):
        return (
            entry.get("number"),
            str(entry.get("title", "") or ""),
            str(entry.get("lane", "") or ""),
            str(entry.get("verify", "") or ""),
        )
    if isinstance(entry, (tuple, list)):
        padded = list(entry) + [""] * (4 - len(entry))
        number = padded[0]
        if isinstance(number, bool) or not isinstance(number, int):
            number = None
        return (number, str(padded[1] or ""), str(padded[2] or ""), str(padded[3] or ""))
    return (None, str(entry or ""), "", "")


def duplicate_problem(child: Any, open_issues: Iterable[Any]) -> str | None:
    """Why ``child`` duplicates an issue already OPEN, or ``None``.

    Two identities count as a duplicate, because either one re-files work the
    board already carries:

    * the same **title** (normalised) — the reliable key, since it survives the
      committed snapshot, which stores titles and no lanes; and
    * the same **lane + Verify:** pair — the child's own identity sentence, for a
      caller that has the live board and therefore both fields.

    The comparison set is supplied by the caller and is never fetched here.
    """
    if not isinstance(child, Mapping):
        return None
    title = _norm(child.get("title"))
    named_lanes = lanes(child)
    lane = named_lanes[0] if len(named_lanes) == 1 else ""
    named_verifies = verify_lines(child)
    verify = named_verifies[0] if len(named_verifies) == 1 else ""
    who = f"child '{str(child.get('title', '') or '').strip() or '(untitled)'}'"
    for entry in open_issues or ():
        number, other_title, other_lane, other_verify = _open_tuple(entry)
        where = f"OPEN #{number}" if number is not None else "an open issue"
        if title and title == _norm(other_title):
            return f"{who} duplicates {where} (same title) — it is already on the board"
        if lane and verify and lane == str(other_lane).strip() and verify == str(other_verify).strip():
            return f"{who} duplicates {where} (same lane + Verify:) — it is already on the board"
    return None


def wave_problems(
    children: Sequence[Any],
    *,
    open_issues: Iterable[Any] = (),
    cap: int = DEFAULT_CAP,
) -> list[str]:
    """Every reason this WAVE cannot be filed — empty when it can.

    All guards run (rather than stopping at the first), so an operator fixing a
    wave sees everything wrong with it in one refusal instead of one per round
    trip. The guards are: the per-child sizing rule, intra-wave duplicates, the
    duplicate guard against the open board, and the wave cap.
    """
    problems: list[str] = []
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        return [f"wave cap {cap!r} is not a positive integer — REFUSED, the cap is never defaulted silently"]

    candidates = list(children or ())
    if not candidates:
        return ["the wave carries no children — nothing to file"]

    for child in candidates:
        problem = sizing_problem(child)
        if problem:
            problems.append(problem)

    seen: dict[str, int] = {}
    for index, child in enumerate(candidates):
        key = _norm(child.get("title")) if isinstance(child, Mapping) else ""
        if not key:
            continue
        if key in seen:
            problems.append(
                f"child '{str(child.get('title', '') or '').strip()}' is filed twice in this wave "
                f"(children {seen[key]} and {index}) — the duplicate guard is intra-wave too"
            )
        else:
            seen[key] = index

    for child in candidates:
        problem = duplicate_problem(child, open_issues)
        if problem:
            problems.append(problem)

    if len(candidates) > cap:
        problems.append(
            f"the wave mints {len(candidates)} children, over the cap of {cap} "
            f"({CAP_ENV}) — split it into waves of at most {cap}"
        )
    return problems


def effective_cap(env: Mapping[str, str] | None = None, *, focus_wave_cap: int | None = None) -> int:
    """The wave cap: ``FLEET_DECOMPOSE_CAP``, else the focus ``wave_cap``, else 12.

    **The precedence is deliberate and stated here** (the issue allows the focus
    fallback only if it is documented): an explicit ``FLEET_DECOMPOSE_CAP``
    always wins, because that is the operator speaking *now*; an unset variable
    defers to the pinned focus's ``wave_cap``, because that is the board speaking
    for this epic; with neither, the module default of 12 applies.

    A variable that is *set* but not a positive integer raises ``ValueError``
    instead of falling back: a misconfigured cap is a refusal, never silently
    defaulted to 12 (the brain reports it and files nothing).
    """
    source = os.environ if env is None else env
    raw = str((source or {}).get(CAP_ENV, "") or "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            raise ValueError(f"{CAP_ENV} must be a positive integer, got {raw!r}") from None
        if value < 1:
            raise ValueError(f"{CAP_ENV} must be a positive integer, got {raw!r}")
        return value
    if isinstance(focus_wave_cap, int) and not isinstance(focus_wave_cap, bool) and focus_wave_cap >= 1:
        return focus_wave_cap
    return DEFAULT_CAP


def _micro(title: str, *, files: Sequence[str] = ("fleet/x.py",)) -> dict[str, Any]:
    """A well-sized child, for the self-control below."""
    return {
        "title": title,
        "lane": "fleet",
        "verify": "pytest -q fleet/tests",
        "criterion": "the thing is true",
        "files": list(files),
    }


def self_control() -> list[str]:
    """Prove every guard here can actually fail (anti-formality, GR-12).

    Each mutant must be caught and each positive control must be clean; a
    mismatch is returned as a problem, so a *caller's* gate fails. A validator
    that cannot reject anything is a formality, and this wave cap is the guard
    that stops a decomposition from exploding the board.
    """
    problems: list[str] = []

    def expect(name: str, condition: bool, detail: str) -> None:
        if not condition:
            problems.append(f"self-control['{name}']: {detail}")

    good = _micro("a micro child")
    expect("accepts-micro", sizing_problem(good) is None, f"a micro child was refused ({sizing_problem(good)})")

    mutants: dict[str, dict[str, Any]] = {
        "no-title": {**good, "title": ""},
        "no-lane": {**good, "lane": ""},
        "two-lanes": {**good, "lanes": ["fleet", "governance"]},
        "comma-lanes": {**good, "lane": "fleet, governance"},
        "no-verify": {**good, "verify": "   "},
        "two-verifies": {**good, "verify": "pytest a\nVerify: pytest b"},
        "no-criterion": {**good, "criterion": ""},
        "two-criteria": {**good, "criteria": ["one", "two"]},
        "no-files": {**good, "files": []},
    }
    for name, mutant in mutants.items():
        problem = sizing_problem(mutant)
        expect(f"sizing-rejects-{name}", problem is not None, f"mutant '{name}' was accepted")
    for name in ("two-lanes", "comma-lanes", "two-verifies", "two-criteria"):
        expect(
            f"sizing-names-the-seam-{name}",
            RESPLITTABLE in str(sizing_problem(mutants[name])),
            f"a re-splittable child ({name}) must be refused as re-splittable, naming the parent seam",
        )

    expect("depth-2-ok", sizing_problem(good, depth=MAX_DEPTH) is None, "depth 2 is epic -> micro-child and must pass")
    expect(
        "depth-3-refused",
        sizing_problem(good, depth=MAX_DEPTH + 1) is not None,
        "a third rung must be refused",
    )

    open_board = [(700, "a micro child", "fleet", "pytest -q fleet/tests")]
    expect(
        "duplicate-by-title",
        duplicate_problem(_micro("a micro child"), open_board) is not None,
        "a child whose title is already open must be refused",
    )
    expect(
        "duplicate-by-lane-and-verify",
        duplicate_problem({**good, "title": "a different title"}, open_board) is not None,
        "a child repeating an open lane + Verify: must be refused",
    )
    expect(
        "not-duplicate",
        duplicate_problem({**_micro("something else entirely"), "verify": "pytest -q registry"}, open_board) is None,
        "an unrelated child must not be refused as a duplicate",
    )
    expect(
        "lane-alone-is-not-a-duplicate",
        duplicate_problem({**_micro("another fleet child"), "verify": "pytest -q registry"}, open_board) is None,
        "sharing a lane is not identity — only lane + Verify: together is a duplicate",
    )

    expect("wave-accepts-12", wave_problems([_micro(f"child {i}") for i in range(12)], cap=12) == [],
           "a wave at the cap must be accepted")
    expect(
        "wave-refuses-13",
        bool(wave_problems([_micro(f"child {i}") for i in range(13)], cap=12)),
        "a wave over the cap must be refused",
    )
    expect("wave-refuses-empty", bool(wave_problems([], cap=12)), "an empty wave must be refused")
    expect(
        "wave-refuses-intra-duplicate",
        bool(wave_problems([_micro("twice"), _micro("twice")], cap=12)),
        "the same child twice in one wave must be refused",
    )
    expect(
        "wave-refuses-a-big-child",
        bool(wave_problems([_micro("ok"), {**_micro("too big"), "lanes": ["a", "b"]}], cap=12)),
        "a re-splittable child anywhere in the wave must fail the wave",
    )

    expect("cap-env-wins", effective_cap({CAP_ENV: "3"}, focus_wave_cap=9) == 3, "the env cap must win")
    expect("cap-focus-fallback", effective_cap({}, focus_wave_cap=9) == 9, "an unset env must defer to the focus")
    expect("cap-default", effective_cap({}) == DEFAULT_CAP, f"an unset env with no focus must be {DEFAULT_CAP}")
    caught = False
    try:
        effective_cap({CAP_ENV: "lots"})
    except ValueError:
        caught = True
    expect("cap-malformed-refused", caught, "a malformed cap must be refused, never silently defaulted")
    caught = False
    try:
        effective_cap({CAP_ENV: "0"})
    except ValueError:
        caught = True
    expect("cap-zero-refused", caught, "a zero cap must be refused")

    # The restated default must not drift from the focus module's own. When
    # `focus` is not importable from here the probe is CANNOT-ASSESS (skipped),
    # never a vacuous pass dressed up as one.
    try:
        import focus as _focus  # noqa: PLC0415 - a local probe of a sibling module

        focus_default = getattr(_focus, "DEFAULT_WAVE_CAP", None)
    except ImportError:
        focus_default = None
    if focus_default is not None:
        expect(
            "default-cap-matches-focus",
            focus_default == DEFAULT_CAP,
            f"focus.DEFAULT_WAVE_CAP is {focus_default}, this module says {DEFAULT_CAP}",
        )
    return problems


__all__ = [
    "CAP_ENV",
    "DEFAULT_CAP",
    "MAX_DEPTH",
    "RESPLITTABLE",
    "criteria",
    "duplicate_problem",
    "effective_cap",
    "files",
    "lanes",
    "self_control",
    "sizing_problem",
    "verify_lines",
    "wave_problems",
]
