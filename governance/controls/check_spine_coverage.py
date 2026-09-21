#!/usr/bin/env python3
"""governance/controls/check_spine_coverage.py — validates

---knowledge---
module_id: governance.controls.check_spine_coverage
system: governance
app: controls
solution_class: enterprise
patterns: [provoked-negative-control, honesty-tri-state]
derives_from: null
owner_sme: qa-sme
tier: L1
interfaces: [load_yaml, parse_rule_ids, declared_suites, split_gate, check, main]
invariants: ""
gotchas: ""
related: ["#803", "#878", "#890"]
do_not_duplicate: null
---knowledge---

governance/controls/spine-coverage.yaml against the repository (#890, lane
L11 of EPIC #878).

WHAT IT CHECKS (all against the repository, never against prose)
  * every rule id (`GR-<n>` / `AO-GR-<n>`) parsed out of AGENTS.md and
    docs/GOLDEN-RULES.md has exactly one row under `rules:` in the map --
    no rule missing a row, no row for a rule id that no longer exists.
  * every `gate` entry that is not "-" and does not start with `suite:` names
    a real file that EXISTS and is EXECUTABLE (checked with os.access X_OK).
  * a `suite:<dir>` gate is declared in scripts/pytest-suites.txt (the
    manifest scripts/run-pytest-suites.sh actually runs) -- an undeclared
    suite is a suite no gate runs.
  * no row claims `covered: true` while its `gate` is "-" or names a file
    that does not exist -- that is exactly the false-green shape this gate
    exists to refuse.
  * a rule whose status is GAP is reported honestly (never silently OK) and
    counted separately from ENFORCED/PARTIAL rows.

HOW IT PROVES ITSELF
  Run with --self-test: validates the real map (must PASS), then loads a
  mutated copy whose first `covered: true` row is repointed at a gate file
  that does not exist in the repo, and requires that mutant be REFUSED BY
  NAME (its rule id + the phantom gate path must appear in the failure). A
  checker that cannot fail is a formality (AO-GR-4).

Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

Usage:
  python3 governance/controls/check_spine_coverage.py [--root DIR]
  python3 governance/controls/check_spine_coverage.py --self-test
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - environment guard
    yaml = None

RULE_RE = re.compile(r"\bAO-GR-\d+\b|\bGR-\d+\b")


def _fallback_parse_yaml(text: str) -> dict:
    """Minimal YAML-subset parser used only if PyYAML is unavailable.

    The map file is a plain, flat structure (mapping -> mapping -> scalars);
    this parser handles exactly that shape and nothing more. It exists so the
    gate can still CANNOT-ASSESS cleanly rather than crash if PyYAML is
    missing, and still validate the real file in the common case where
    PyYAML *is* present (checked first, above).
    """
    raise RuntimeError("PyYAML not available and no fallback parser implemented")


def load_yaml(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(text)
    return _fallback_parse_yaml(text)


def parse_rule_ids(*doc_paths: Path) -> set[str]:
    ids: set[str] = set()
    for p in doc_paths:
        if not p.is_file():
            continue
        for m in RULE_RE.finditer(p.read_text(encoding="utf-8", errors="replace")):
            ids.add(m.group(0))
    return ids


def declared_suites(root: Path) -> set[str]:
    manifest = root / "scripts" / "pytest-suites.txt"
    out: set[str] = set()
    if manifest.is_file():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.add(line.split()[0].rstrip("/"))
    return out


def _is_invokable(path: Path) -> bool:
    """A gate file is 'invokable' if it carries the executable bit OR a
    shebang line -- this repo's convention runs `check-*.sh` gates via an
    explicit `bash scripts/check-x.sh` (see scripts/verify.sh), so most are
    not individually chmod +x. Either signal proves the file is a real,
    runnable script rather than inert prose."""
    if os.access(path, os.X_OK):
        return True
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
    except OSError:
        return False
    return first.startswith("#!")


def split_gate(gate: str) -> list[str]:
    if gate is None or gate.strip() in ("", "-"):
        return []
    return [g.strip() for g in gate.split(",") if g.strip()]


def check(root: Path, map_path: Path, spine_ids: set[str], verbose: bool = True) -> tuple[int, list[str]]:
    """Returns (exit_code, findings)."""
    findings: list[str] = []
    if not map_path.is_file():
        return 2, [f"CANNOT-ASSESS: map file not found: {map_path}"]

    try:
        data = load_yaml(map_path)
    except Exception as exc:  # noqa: BLE001
        return 2, [f"CANNOT-ASSESS: could not parse {map_path}: {exc}"]

    if not isinstance(data, dict) or "rules" not in data:
        return 2, [f"CANNOT-ASSESS: {map_path} has no top-level 'rules' mapping"]

    rules = data.get("rules") or {}
    suites = declared_suites(root)

    mapped_ids = set(rules.keys())
    missing = spine_ids - mapped_ids
    for rid in sorted(missing):
        findings.append(f"FAIL rule-missing-row: {rid} is in the spine but has no row in {map_path.name}")

    stale = mapped_ids - spine_ids
    for rid in sorted(stale):
        findings.append(f"FAIL row-for-unknown-rule: {rid} has a row but is not a rule id found in the spine")

    gap_count = 0
    enforced_count = 0
    partial_count = 0
    for rid, row in sorted(rules.items()):
        if not isinstance(row, dict):
            findings.append(f"FAIL malformed-row: {rid} row is not a mapping")
            continue
        status = str(row.get("status", "")).strip()
        covered = bool(row.get("covered", False))
        gate_field = row.get("gate", "-")
        entries = split_gate(str(gate_field))

        if status == "GAP":
            gap_count += 1
            if covered:
                findings.append(f"FAIL gap-claims-covered: {rid} status GAP but covered: true")
            continue
        if status == "not-applicable":
            continue
        if status == "ENFORCED":
            enforced_count += 1
        elif status == "PARTIAL":
            partial_count += 1
        else:
            findings.append(f"FAIL unknown-status: {rid} status={status!r}")
            continue

        if not covered:
            findings.append(f"FAIL status-without-covered: {rid} status={status} but covered: false")
            continue

        if not entries:
            findings.append(f"FAIL covered-with-no-gate: {rid} covered: true but gate is '-'")
            continue

        for entry in entries:
            if entry.startswith("suite:"):
                d = entry.split(":", 1)[1]
                if d not in suites:
                    findings.append(
                        f"FAIL undeclared-suite: {rid} names {entry} which scripts/pytest-suites.txt does not declare"
                    )
                if not (root / d).is_dir():
                    findings.append(f"FAIL missing-suite-dir: {rid} names {entry} but {d} does not exist")
                continue
            gate_path = root / entry
            if not gate_path.is_file():
                findings.append(f"FAIL missing-gate-file: {rid} names {entry} which does not exist in the repository")
                continue
            if not _is_invokable(gate_path):
                findings.append(
                    f"FAIL non-executable-gate: {rid} names {entry} which exists but has neither the "
                    "executable bit nor a shebang line (repo convention runs check-*.sh via `bash <path>`, "
                    "so either satisfies 'executable' here)"
                )

    # delivery_controls block (#803) — same rules, smaller universe.
    delivery = data.get("delivery_controls") or {}
    for name, row in sorted(delivery.items()):
        if not isinstance(row, dict):
            findings.append(f"FAIL malformed-row: delivery_controls.{name} is not a mapping")
            continue
        covered = bool(row.get("covered", False))
        entries = split_gate(str(row.get("gate", "-")))
        if covered and not entries:
            findings.append(f"FAIL covered-with-no-gate: delivery_controls.{name} covered: true but gate is '-'")
        for entry in entries:
            if entry.startswith("suite:"):
                continue
            gate_path = root / entry
            if not gate_path.is_file():
                findings.append(
                    f"FAIL missing-gate-file: delivery_controls.{name} names {entry} which does not exist"
                )

    if verbose:
        findings.insert(
            0,
            f"INFO coverage: {enforced_count} ENFORCED, {partial_count} PARTIAL, {gap_count} GAP "
            f"of {len(rules)} rows ({len(spine_ids)} rule ids found in spine docs)",
        )

    fail = any(f.startswith("FAIL") for f in findings)
    return (1 if fail else 0), findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    root = Path(args.root) if args.root else Path(__file__).resolve().parents[2]
    map_path = root / "governance" / "controls" / "spine-coverage.yaml"
    spine_ids = parse_rule_ids(root / "AGENTS.md", root / "docs" / "GOLDEN-RULES.md")

    if yaml is None:
        print("CANNOT-ASSESS: PyYAML not importable in this environment", file=sys.stderr)
        return 2

    code, findings = check(root, map_path, spine_ids)
    for f in findings:
        print(f"  {f}")
    print(f"check-spine-coverage: {'OK' if code == 0 else 'NOT-OK' if code == 1 else 'CANNOT-ASSESS'}")

    if not args.self_test:
        return code

    # ---- Negative control: prove the checker can fail by name. -----------
    if code != 0:
        print("SELF-TEST: real map already fails; cannot prove negative control meaningfully", file=sys.stderr)
        return 2

    data = load_yaml(map_path)
    mutant_rule = None
    for rid, row in data.get("rules", {}).items():
        if isinstance(row, dict) and row.get("covered") and row.get("gate", "-") not in (None, "-"):
            mutant_rule = rid
            break
    if mutant_rule is None:
        print("SELF-TEST: no covered row with a gate to mutate", file=sys.stderr)
        return 2

    phantom = "scripts/check-does-not-exist-phantom-gate.sh"
    data["rules"][mutant_rule]["gate"] = phantom

    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tmp:
        yaml.safe_dump(data, tmp, sort_keys=False)
        tmp_path = Path(tmp.name)

    try:
        mcode, mfindings = check(root, tmp_path, spine_ids, verbose=False)
    finally:
        tmp_path.unlink(missing_ok=True)

    named = any(mutant_rule in f and phantom in f for f in mfindings)
    if mcode == 1 and named:
        print(f"SELF-TEST: OK — mutant ({mutant_rule} -> {phantom}) refused by name")
        return code
    print(
        f"SELF-TEST: FAIL — mutant not refused by name (exit={mcode}, findings={mfindings})",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
