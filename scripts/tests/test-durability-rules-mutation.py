#!/usr/bin/env python3
"""Mutation proof for scripts/check-fleet-durability-rules.sh (AO-GR-4).

Each mutant removes ONE thing a rule depends on. The mutation must actually land
(asserted by comparing the file's sha256 before/after, and by asserting the
replaced text differs) and the check must then FAIL and NAME the rule. A mutant
that does not change the file would make this harness vacuous — the exact
false-green the rules themselves forbid — so it is a harness error, not a pass.
"""
import hashlib, pathlib, subprocess, sys

WT = pathlib.Path("/home/akushnir/ao-worktrees/ao-gov-fleet-1789427408")
CHECK = "scripts/check-fleet-durability-rules.sh"

MUTANTS = [
    ("delete the AO-GR-25 drift rule heading", "docs/GOLDEN-RULES.md",
     "### AO-GR-25 — Drift is measured against the remote, and never fails open",
     "### AO-GR-25x — REMOVED"),
    ("strip AO-GR-21's Verify block (make it a formality)", "docs/GOLDEN-RULES.md",
     "**Verify.**\n- Per-directive attempts are persisted and survive a loop restart; attempts are",
     "**NoVerify.**\n- Per-directive attempts are persisted and survive a loop restart; attempts are"),
    ("remove AO-GR-23's originating issue (lose provenance)", "docs/GOLDEN-RULES.md",
     "**Origin.** Issue #740 (measured 2026-09-14: 46 unpushed commits, 31 worktrees).",
     "**Origin.** (unrecorded)"),
    ("un-cite AO-GR-27 from the canonical doctrine", "AGENTS.md",
     "(Spine: AO-GR-27.)",
     "(Spine: removed.)"),
    ("rip out the runner preflight control", "fleet/terminal.py",
     "def preflight(runner: str) -> tuple[bool, str]:",
     "def not_a_preflight(runner: str) -> tuple[bool, str]:"),
    ("delete the watchdog drift classifier", "fleet/watchdog.py",
     "def decide(",
     "def decide_removed("),
    ("advise the operator to HUP the loop", "fleet/README.md",
     "kill -TERM \"$(pgrep -f 'fleet/terminal.py run')\"",
     "kill -HUP \"$(pgrep -f 'fleet/terminal.py run')\""),
]


def digest(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main() -> int:
    originals = {}
    for _, rel, _, _ in MUTANTS:
        originals.setdefault(rel, (WT / rel).read_text())

    base = subprocess.run(["bash", CHECK], cwd=WT, capture_output=True, text=True)
    print(f"baseline: rc={base.returncode}  "
          f"{base.stdout.strip().splitlines()[-1][:96]}")
    if base.returncode != 0:
        print("HARNESS ERROR: the unmutated check is not green", file=sys.stderr)
        return 2

    failures = []
    for desc, rel, old, new in MUTANTS:
        path = WT / rel
        path.write_text(originals[rel])           # restore before each mutant
        before = digest(path)
        src = path.read_text()
        if src.count(old) != 1:
            print(f"!! SKIP  {desc}: anchor appears {src.count(old)}x (not 1)")
            failures.append(desc)
            continue
        mutated = src.replace(old, new)
        if mutated == src:
            print(f"!! SKIP  {desc}: replacement produced no change")
            failures.append(desc)
            continue
        path.write_text(mutated)
        after = digest(path)
        if before == after:
            print(f"!! SKIP  {desc}: digest unchanged ({before}) — vacuous")
            failures.append(desc)
            continue

        run = subprocess.run(["bash", CHECK], cwd=WT, capture_output=True, text=True)
        fail_lines = [l for l in run.stdout.splitlines() if "FAIL" in l]
        caught = run.returncode != 0 and fail_lines
        status = "CAUGHT" if caught else "MISSED"
        print(f"  {status:7} {desc}")
        print(f"          sha {before} -> {after}  rc={run.returncode}  "
              f"fail_lines={len(fail_lines)}")
        if fail_lines:
            print(f"          first: {fail_lines[0].strip()[:96]}")
        if not caught:
            failures.append(desc)

    for rel, text in originals.items():
        (WT / rel).write_text(text)               # restore everything

    restored = subprocess.run(["bash", CHECK], cwd=WT, capture_output=True, text=True)
    ok_restore = all(digest(WT / r) == hashlib.sha256(t.encode()).hexdigest()[:16]
                     for r, t in originals.items())
    print(f"\nrestored: rc={restored.returncode}  "
          f"{restored.stdout.strip().splitlines()[-1][:96]}")
    if not ok_restore:
        print("HARNESS ERROR: source files not byte-restored", file=sys.stderr)
        return 2

    print(f"\nmutants: {len(MUTANTS)}  caught: {len(MUTANTS) - len(failures)}  "
          f"missed: {len(failures)}")
    if failures:
        print("MUTATION PROOF FAILED — these mutants went undetected:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("MUTATION PROOF OK — every mutant was caught, and the tree restores green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
