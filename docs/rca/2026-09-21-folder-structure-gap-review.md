# Folder-structure gap review — depth, orphans, organization (no epic filed)

## Scope

Platform-SME hygiene pass over the repo's tracked file tree: depth
distribution, orphaned files/empty directories, and organization quality
against the declared five-pillar architecture (`AGENTS.md` §"Directory
layout (pillar-aligned)"). Read-only; no files moved or deleted.

## Depth distribution (`git ls-files`, 3224 tracked files)

| path segments | file count |
|---|---|
| 1 | 27 |
| 2 | 413 |
| 3 | 1112 |
| 4 | 1164 |
| 5 | 418 |
| 6 | 77 |
| 7 | 13 |

Max depth is 7. All 13 depth-7 paths were inspected individually — each is a
genuinely structured leaf (a `SKILL.md` under a plugin/library category, a
`role.schema.json` under a parity fixture tree, or a consumer-repo template
under `control-plane/sdk/template/`). None repeats a directory name inside
itself (no `foo/bar/foo/bar/` accidental nesting). **Finding: none.**

## Root-level tracked files

27 root files, all standard project metadata/config (`AGENTS.md`,
`CLAUDE.md`, `README.md`, `Makefile`, `.gitignore`, `pytest.ini`,
`architecture.yaml`, `module.json`, etc.). No stray scripts or app config
that belongs in `scripts/`/`infra/`. **Finding: none.**

## Orphans

- **Empty tracked-adjacent directories:** none. (Git doesn't track empty
  dirs, so a fresh `checkout -b` from `origin/master` cannot itself produce
  one — this null result is expected of the checkout, not a hygiene
  finding.)
- **Single-file directories:** 96 of 472 tracked directories hold exactly
  one file. All 96 were printed and reviewed: they are per-module `README.md`
  files, per-provider `module.json` catalog entries
  (`gateway/catalog/modules/<provider>/module.json` ×9), per-office prompt
  files (`registry/personas/offices/<office>/prompts/`), and single-purpose
  schema/config/fixture files. Only one directory is single-file *and*
  trivial in content (`integrations/paperclip/adapters/__init__.py`), but
  that directory also holds six populated subdirectories — it's a normal
  Python package marker, not an orphan.
- **Reference sample:** 20 files sampled from depth 4–5 outside
  `tests/`/`docs/` were grepped by basename across the tree. All resolved to
  either direct code/config references (`model.py` 181 refs, `variables.tf`
  40 refs, `document.schema.json` 30 refs) or single self-only hits that are
  data files loaded by directory-scan convention rather than by literal
  name (persona cards, prompt bodies, fixture JSON) — the same pattern used
  throughout `registry/personas/` and `governance/*/fixtures/`. No dead
  files found in the sample.
- **`governance/sync/seeds/canonical/vendor/unrelated-asset.txt`** — name
  suggested an orphan; confirmed it's a deliberate "not part of canonical
  set" test fixture, referenced by `governance/sync/tests/`.

**Finding: none actionable.**

## Structure vs. declared architecture

All five declared pillars are populated with real code, not placeholder
scaffolding:

| pillar | tracked files | dominant extension |
|---|---|---|
| `registry/` | 284 | `.yaml` (124), `.py` (84) |
| `gateway/` | 222 | `.py` (184) |
| `engine/` | 137 | `.py` (127) |
| `guardrails/` | 188 | `.py` (133) |
| `telemetry/` | 141 | `.py` (117) |

Each pillar's extension mix is dominated by `.py` execution code, not
documentation/schema alone — no structure-vs-reality mismatch (no pillar is
an empty or doc-only placeholder masking real code living elsewhere).

## Conclusion

This is a hygiene pass, not a rewrite. Every check in the review protocol
(depth, empty dirs, single-file dirs, sampled dead-file references, root
stray files, pillar-vs-reality) came back clean. **No child issues filed
and no new epic opened** — manufacturing findings against a repo that
passes its own structure checks would violate the review's own instruction
to report a clean result plainly. Existing epic #1908 ("codebase hygiene —
headers, tagging, wrappers, env-var patterns, templates") remains the
correct home if a future pass turns up real structural drift; this review
found none.
