# Corpus — real incident artifacts (fail + pass)

Issue #28, acceptance criterion 5: *corpus of real incident texts (fail +
pass) used to tune guards — never only invented examples.* The anti-formality
analyzer and the honesty test suite are tuned and validated against these
**real captured artifacts**, not only the synthetic fixtures under
[`../fixtures/`](../fixtures/). Every artifact is a guard shape that was found
in production (or in a real fleet incident) and is recorded here with its
provenance.

**Source material (read-only, never modified):**

| Repo | Path(s) |
|---|---|
| `kushin77/leaderboard` | `scripts/guard/check-formality.sh` (documented incident spellings #1–#4 and the never-fail-function rule) |
| `kushin77/leaderboard` | `scripts/guard/guard-self-match.sh` (self-match incident #91) |
| `kushin77/leaderboard` | `scripts/qa/verify-negative-controls.sh` (fail-closed methodology) |
| `kushin77/leaderboard` | `tests/blockproof/` and `tests/negative/` (blockproof + negative-control doctrine) |
| `kushin77/CMR` | `guardrails/sweep/` + `tests/` (sweep methodology) |

Artifacts are **adapted** (the shapes distilled from the incidents), not
copied; each file names its source shape in its header.

## fail/ — guards that must be caught

| Artifact | Real incident / source shape | Expected analyzer rule(s) |
|---|---|---|
| `check_all_read.sh` | check-formality spelling #1: `[ -e "$f" ] \|\| continue` uncounted — a tool visited ZERO entries and reported "already matches" | `uncounted_skip`, `never_fails_script` |
| `check_results_present.sh` | check-formality spelling #3: blocking gates whose every artifact was absent echoed "optional" and exited 0 | `never_fails_script` (absence-gated optional else) |
| `check_provider_health.sh` | check-formality spelling #4: SKIP counted as a pass — health check reported UP for a provider never configured | `never_fails_script`, `skip_counted_as_pass` |
| `check_everything_clean.sh` | check-formality function rule: found and not-found paths return the same code, 0 | `never_fails_function` |
| `scan_service_log.sh` | guard-self-match incident #91: grep pattern also appears in the comment explaining that grep | `self_match` |

## pass/ — honest guards that must be left alone

| Artifact | Real honest shape | Analyzer expectation |
|---|---|---|
| `check_clean.sh` | guard that ships a should-fail path and a CANNOT-ASSESS path | clean |
| `check_artifacts.sh` | loop that counts what it examined and fails on absence | clean |
| `check_fail_closed.sh` | fail-closed tri-state guard (exit 2 on unresolvable, exit 1 on violation) | clean |

The corpus is exercised by [`tests/test_corpus.py`](../tests/test_corpus.py):
every `fail/` artifact must trip the analyzer (the guard genuinely fires on
real bad input) and every `pass/` artifact must clear it (no false positives on
real good input). Both directions are asserted — never only invented examples.
