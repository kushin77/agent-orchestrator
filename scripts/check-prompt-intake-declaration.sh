#!/usr/bin/env bash
# check-prompt-intake-declaration.sh — the interactive-prompt-intake decision, enforced
# (issue #1516, child of epic #1510; decision recorded in ADR-0034).
#
# WHAT THIS IS
#   ADR-0034 decides that an interactive Claude Code prompt does NOT route through
#   this repository's orchestrator (`fleet/brain.py` + `governance/dispatch/`), and
#   that the decision is DECLARED in a project `.claude/settings.json` rather than
#   left as silence. A declaration nothing checks is a formality (GR-12, AO-GR-4),
#   and AO-GR-28/GR-29 make the same point for every governed artifact: the rule is
#   constitutional only while a gate fails by name the moment the declaration stops.
#
#   It refuses, BY NAME:
#     * `.claude/settings.json` missing, unparseable, or missing the
#       `_orchestrator_prompt_intake` declaration object;
#     * a decision/scope that is not the vocabulary ADR-0034 declares;
#     * an `authority` that dangles, is not an `accepted` ADR, or whose `id` does
#       not match its own filename;
#     * an authority ADR that has lost one of its house sections — including
#       `## Promotion decision`, which is the section issue #1516's own brief
#       cites, so stripping it would silently break a live reference;
#     * ANY project-scope hook event, named individually. This is the load-bearing
#       half: the decision is "non-routing", so a project hook appearing while
#       ADR-0034 is `accepted` means the declaration and the behaviour disagree.
#       It is refused rather than tolerated because a hook can BLOCK a session
#       (`fleet/hooks/claude-beat.sh:89`), and every lane worktree is a checkout of
#       this repository, so the file is live in all of them at once.
#     * `.claude/README.md` missing, or no longer naming the authority ADR.
#
# WHY IT IS SHAPED LIKE THIS (the no-false-green doctrine, GR-12)
#   A check that cannot fail is a formality, so this gate proves itself on EVERY
#   run, before it reports on the repository:
#
#     1. the REAL tree must pass — otherwise a rule that matches everything would
#        read as a green gate (the vacuity half);
#     2. a provocation of each rule must be REFUSED, by name;
#     3. every provocation must be shown to have CHANGED the bytes it mutated —
#        a mutant that never applied would make its refusal meaningless;
#     4. at least one benign mutation (whitespace re-serialisation of the same
#        JSON document) must still PASS — so the gate is shown to fire BOTH ways,
#        and cannot pass by refusing every mutation it is handed.
#
#   The provocations run the SAME `validate()` the repository run uses, never a
#   copy, so what is proven is the code path that runs.
#
# HERMETIC
#   Offline and deterministic: no network, no `vendor/CMR` submodule, no vendored
#   seed, no host state. It reads four tracked paths and a scratch copy of them.
#
# EXIT CONTRACT (the repo's honesty tri-state, guardrails/honesty)
#   0  OK               the declaration holds and every provocation was refused
#   1  NOT-OK           a named refusal; printed on stderr, never swallowed
#   2  CANNOT-ASSESS    python3 is absent, or the repository root cannot be
#                       resolved — NEVER reported as a pass
#
# Usage: bash scripts/check-prompt-intake-declaration.sh [--self-test]
#   (--self-test is accepted and is a no-op beyond the default run: the
#    provocations ALWAYS run, because a gate whose self-test is optional is a
#    gate that can be green without proving it can fail.)
#
# ---knowledge---
# module_id: scripts.check-prompt-intake-declaration
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [self-proving-gate, no-false-green]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: "the provocations always run -- --self-test is a no-op beyond the default"
# gotchas: ""
# related: ["#1516"]
# do_not_duplicate: null
# ---knowledge---

set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)" || exit 2
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-prompt-intake-declaration: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

work=""
cleanup() {
  [ -n "$work" ] && rm -rf "$work"
  return 0
}
trap cleanup EXIT

work="$(mktemp -d "${TMPDIR:-/tmp}/ao-prompt-intake.XXXXXX")" || exit 2

# The three governed artifacts are named in ONE place, so a rename lands here.
settings=".claude/settings.json"
readme=".claude/README.md"
authority="docs/decision-records/ADR-0034-interactive-prompt-intake-non-routing.md"

python3 - "$root" "$work" "$settings" "$readme" "$authority" <<'PY'
"""Validate the interactive-prompt-intake declaration, and provoke every refusal."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1])
work = Path(sys.argv[2])
settings_rel, readme_rel, authority_rel = sys.argv[3], sys.argv[4], sys.argv[5]

DECISION = "non-routing"
SCOPE = "interactive-claude-code-sessions"
HOUSE_SECTIONS = ("## Status", "## Context", "## Decision",
                  "## Promotion decision", "## Consequences")

failures: list[str] = []


def fail(message: str) -> None:
    failures.append(message)


def read_text(path: Path, label: str) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"{label} is unreadable: {exc}")
        return None


def front_matter(text: str) -> dict[str, str]:
    """Parse the ADR's leading `---` block into a flat key/value map."""
    out: dict[str, str] = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return out
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip().strip("[]").strip()
    return out


def validate(base: Path) -> None:
    """Every rule. Appends a named finding per breach; silence means OK."""
    settings = base / settings_rel
    readme = base / readme_rel

    raw = read_text(settings, settings_rel) if settings.is_file() else None
    if raw is None:
        if not settings.is_file():
            fail(f"{settings_rel} is missing — the decision must be DECLARED, "
                 "not left as silence (ADR-0034, issue #1516)")
        return

    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"{settings_rel} is not valid JSON: {exc}")
        return
    if not isinstance(doc, dict):
        fail(f"{settings_rel} is not a JSON object")
        return

    decl = doc.get("_orchestrator_prompt_intake")
    if not isinstance(decl, dict):
        fail(f"{settings_rel} carries no `_orchestrator_prompt_intake` object — "
             "the declaration ADR-0034 requires is gone")
        return

    if decl.get("decision") != DECISION:
        fail(f"{settings_rel}: `decision` is {decl.get('decision')!r}, "
             f"expected {DECISION!r}")
    if decl.get("scope") != SCOPE:
        fail(f"{settings_rel}: `scope` is {decl.get('scope')!r}, expected {SCOPE!r}")

    declared = decl.get("authority")
    if not isinstance(declared, str) or not declared.strip():
        fail(f"{settings_rel}: `authority` names no ADR")
    else:
        adr = base / declared
        if not adr.is_file():
            fail(f"{settings_rel}: `authority` dangles — {declared} does not exist")
        else:
            text = read_text(adr, declared)
            if text is not None:
                meta = front_matter(text)
                if meta.get("status") != "accepted":
                    fail(f"{declared}: status is {meta.get('status')!r}, "
                         "expected 'accepted' — a decision record that is not "
                         "accepted cannot be the authority for a live declaration")
                adr_id = meta.get("id", "")
                if not adr.stem.startswith(f"{adr_id}-"):
                    fail(f"{declared}: front-matter id {adr_id!r} does not match "
                         "its own filename")
                for section in HOUSE_SECTIONS:
                    if section not in text:
                        fail(f"{declared}: lost the house section `{section}`")

    # The load-bearing half: project scope must stay hook-free while the decision
    # is "non-routing". A hook can block a session (claude-beat.sh:89) and this
    # file is present in every lane worktree at once.
    if "hooks" not in doc:
        fail(f"{settings_rel} does not declare `hooks` at all — declare `{{}}` so "
             "the absence of project hooks is stated, not implied")
    else:
        hooks = doc.get("hooks")
        if not isinstance(hooks, dict):
            fail(f"{settings_rel}: `hooks` is not an object")
        elif hooks:
            for event in sorted(hooks):
                fail(f"{settings_rel}: project-scope hook `{event}` is declared "
                     "while the decision is 'non-routing' — arrive with a "
                     "superseding ADR, or remove the hook")

    if not readme.is_file():
        fail(f"{readme_rel} is missing — the decision needs its prose home")
    else:
        prose = read_text(readme, readme_rel)
        if prose is not None and Path(authority_rel).name not in prose:
            fail(f"{readme_rel} no longer names {Path(authority_rel).name}")


# --- arm 1: the REAL tree must pass (the vacuity half) -----------------------
validate(root)
if failures:
    for finding in failures:
        print(f"check-prompt-intake-declaration: FAIL — {finding}", file=sys.stderr)
    print("check-prompt-intake-declaration: NOT-OK — the declaration does not hold "
          "on the real tree", file=sys.stderr)
    sys.exit(1)
print("check-prompt-intake-declaration: the declaration holds on the real tree "
      "(decision=non-routing, authority accepted, no project hook, readme names it)")

# --- arms 2..n: each rule is provoked, and each provocation is measured ------
def scripted(base: Path) -> None:
    """Copy the three governed artifacts into a scratch root, same layout."""
    for rel in (settings_rel, readme_rel, authority_rel):
        src = root / rel
        dst = base / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)


def rewrite(path: Path, new_text: str) -> bool:
    """Write, and REPORT whether the bytes actually changed (a no-op mutant
    would make its refusal meaningless)."""
    before = path.read_bytes()
    path.write_text(new_text, encoding="utf-8")
    return path.read_bytes() != before


def edit_json(before: str, mutate) -> str:
    doc = json.loads(before)
    mutate(doc)
    return json.dumps(doc, indent=2) + "\n"


provocations: list[tuple[str, bool, bool]] = []  # (label, refused?, changed?)


def provoke(label: str, mutate, *, expect_refusal: bool = True) -> None:
    base = work / label.replace(" ", "_").replace("/", "_")
    base.mkdir(parents=True, exist_ok=True)
    scripted(base)
    raw = (base / settings_rel).read_text(encoding="utf-8")
    changed = mutate(base, raw)
    del failures[:]
    validate(base)
    refused = bool(failures)
    detail = failures[0] if failures else ""
    provocations.append((label, refused, changed))
    print(f"  {label:<46} refused={str(refused):<5} changed={str(changed):<5} "
          f"expected={str(expect_refusal):<5} {detail}")
    if expect_refusal and not refused:
        print(f"check-prompt-intake-declaration: FAIL — the provocation "
              f"{label!r} was NOT refused; the rule is inert", file=sys.stderr)
    if not changed:
        print(f"check-prompt-intake-declaration: FAIL — the provocation "
              f"{label!r} changed no bytes; it tested nothing", file=sys.stderr)
    if refused is not expect_refusal:
        sys.exit(1)


print("provocations (each must be refused, and each must have really changed the bytes):")

provoke("truncated settings JSON",
        lambda b, raw: rewrite(b / settings_rel, raw[: len(raw) // 2]))

provoke("declaration object removed",
        lambda b, raw: rewrite(b / settings_rel, edit_json(
            raw, lambda d: d.pop("_orchestrator_prompt_intake"))))

provoke("decision flipped to 'routing'",
        lambda b, raw: rewrite(b / settings_rel, edit_json(
            raw, lambda d: d["_orchestrator_prompt_intake"].__setitem__(
                "decision", "routing"))))

provoke("project hooks block left undeclared",
        lambda b, raw: rewrite(b / settings_rel, edit_json(
            raw, lambda d: d.pop("hooks"))))


def _add_hook(b: Path, raw: str) -> bool:
    def mutate(doc: dict) -> None:
        doc["hooks"] = {"UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "true"}]}]}
    return rewrite(b / settings_rel, edit_json(raw, mutate))


provoke("a UserPromptSubmit project hook added", _add_hook)

provoke("authority ADR dangles",
        lambda b, raw: rewrite(b / settings_rel, edit_json(
            raw, lambda d: d["_orchestrator_prompt_intake"].__setitem__(
                "authority", "docs/decision-records/ADR-9999-does-not-exist.md"))))


def _strip_section(b: Path, raw: str) -> bool:
    text = (b / authority_rel).read_text(encoding="utf-8")
    return rewrite(b / authority_rel,
                   text.replace("## Promotion decision", "## Promotion"))


provoke("authority ADR loses `## Promotion decision`", _strip_section)


def _unaccept(b: Path, raw: str) -> bool:
    text = (b / authority_rel).read_text(encoding="utf-8")
    return rewrite(b / authority_rel, text.replace("status: accepted",
                                                   "status: proposed", 1))


provoke("authority ADR is not accepted", _unaccept)


def _unnamed(b: Path, raw: str) -> bool:
    text = (b / readme_rel).read_text(encoding="utf-8")
    return rewrite(b / readme_rel,
                   text.replace(Path(authority_rel).name, "the ADR"))


provoke("README stops naming the authority", _unnamed)

# --- the other direction: a BENIGN mutant must still PASS -------------------
# Whitespace-only re-serialisation of the same document. If the gate refuses
# this too, it is refusing every mutation rather than the breach under test.
def _reserialize(b: Path, raw: str) -> bool:
    doc = json.loads(raw)
    return rewrite(b / settings_rel, json.dumps(doc, indent=4, sort_keys=True) + "\n")


provoke("benign: JSON re-serialised (must PASS)", _reserialize,
        expect_refusal=False)

# --- verdict ----------------------------------------------------------------
breaches = [(label, refused, changed) for label, refused, changed in provocations
            if refused]
benign = [(label, refused, changed) for label, refused, changed in provocations
          if not refused]
expected_breaches = len(provocations) - 1
if len(breaches) != expected_breaches:
    print(f"check-prompt-intake-declaration: FAIL — {len(breaches)} of "
          f"{expected_breaches} breaches were refused; the rest are inert",
          file=sys.stderr)
    sys.exit(1)
if len(benign) != 1:
    print("check-prompt-intake-declaration: FAIL — the benign mutant did not pass; "
          "the gate refuses every mutation, not the breach under test",
          file=sys.stderr)
    sys.exit(1)
unmeasured = [label for label, _, changed in provocations if not changed]
if unmeasured:
    print(f"check-prompt-intake-declaration: FAIL — these provocations changed no "
          f"bytes and therefore tested nothing: {', '.join(unmeasured)}",
          file=sys.stderr)
    sys.exit(1)

print(f"check-prompt-intake-declaration: self-test OK — "
      f"{len(breaches)}/{expected_breaches} breaches refused by name, "
      f"{len(benign)} benign mutant still passed, and every provocation changed "
      "the bytes it mutated")
print("check-prompt-intake-declaration: OK")
PY
rc=$?
if [ "$rc" -ne 0 ]; then
  exit "$rc"
fi
exit 0
