# Contributing to CMR

## Connections

- **Owner-lane:** qa-sme
- **Class:** class
- **Connects-to:** consumes=none; called-by=none; gates=none
- **Env:** none
- **Updated-by:** qa-sme (2026-09-12)
- **Landed-by:** 9d61bf5

> **New to the CMR program?** Start at the official onboarding process —
> [`docs/ONBOARDING.md`](docs/ONBOARDING.md) — it routes every role (owner, SME, lane agent,
> vendor, spoke consumer, repo role) to its canonical instruction files, checklist, gate, and
> "onboarded" record. This file is the human contributor's working guide **once onboarded**.

CMR is the **hub / control plane** for the `kushin77` ecosystem. Start here:

1. **`AGENTS.md`** — how AI agents work in this repo (canonical, read first).
2. **`GOLDEN-RULES.md`** — the non-negotiable rules (GR-1…GR-23).
3. **`board/INTENT.md`** — requirements `R#` and non-goals `NG#`; cite them.
4. **`docs/EXECUTION-PLAN.md`** + `fleet/MANIFEST.tsv` — lane ownership.

## Branching

- `main` is **protected** — no direct pushes, no force-push, no mass rewrites.
- All work happens on a topic branch named per `docs/DEFAULTS.md`:
  `cmr/<lane>` or `topic/<id>` (e.g. `iac/CMR-104-branch-protection`).
- One branch per issue; merged and deleted when the PR lands.
- Merge/approve rule (self-merge ban + autonomous-merge carve-out) is canonical
  in `GOLDEN-RULES.md` GR-4 (NG2). Open the PR; GR-4 governs who/what merges it.

## Workflow

1. **Pick an issue.** Prefer `fleet-ready` issues — they carry their own
   `Verify:` command. State the goal and success criteria before acting.
2. **Stay in your lane.** Edit only files your issue owns (GR-3). Smallest
   focused diff; no `TODO`/`FIXME`, no debug leftovers, no unused imports.
3. **Make the change**, then run the issue's `Verify:` command **and** the hub
   gate:
   ```bash
   make verify          # gate of record: shell syntax + markdown lint + YAML
   ```
   (Plus any issue-specific command, e.g. `bash -n <script>`,
   `python3 -m json.tool <file>.json`, or the target named in the issue.)
4. **Declare AI assistance** on the PR (CMR-505). State the runtime/agent used
   and the model tier — e.g. `AI-assistance: Copilot (Relentless, L1)`. Every
   AI-originated PR declares this; no exceptions.
5. **Report evidence**, never an unverified "done": the exact command + output,
   files touched, and the R#/issue it closes (GR-12).

## PR checklist

- [ ] Branch name matches `/<lane>/<issue>-<slug>`; based on latest `main`
- [ ] Touches only files owned by the issue's lane (GR-3)
- [ ] Issue `Verify:` command ran and output is included
- [ ] `make verify` green locally
- [ ] No secrets / credentials / `*.tfstate` / build artifacts committed (GR-6)
- [ ] AI-assistance + runtime declared (CMR-505)
- [ ] ADR updated if this changes a recorded decision (see
      `docs/decision-records/README.md`)
- [ ] Docs kept in sync (architecture / adoption / distribution)
- [ ] `Closes #<issue>` referenced

## Governance asks

1. **Hub never hosts spoke code** (GR-1) — no vendored live apps.
2. **IaC over console** (GR-5) — infra changes land as Terraform PRs, not clicks.
3. **AI guardrails ship everywhere** (GR-9) — new governed repos carry the
   instruction-file set.
4. **Provenance first** (GR-10) — canibalized assets record source.
5. **Lessons flow back** (GR-11) — a spoke fix returns as a hub PR.
