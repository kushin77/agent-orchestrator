# RCA-0007 — a go-live declared for months, never exercised

| Field | Value |
|---|---|
| RCA id | `RCA-0007` |
| Incident | `INC-0007` |
| Origin | `#607` (residual `#1029`) |
| Severity | `high` |
| Owner | the AgentConsole hosting lane |
| Reviewed | `2026-09-17` |

## Impact

Epic #607's headline acceptance — *"`ai.purebliss.app` serves the fleet
single-pane-of-glass"* — was unreachable for months while every artifact
declared it was one deploy away. When the go-live was finally run, the console
image **could not boot**: `portal/Dockerfile` installed `PyYAML` but not
`cryptography`, and the boot path (`ConsoleSso.__init__` → `identity.sso.tokens`
→ `identity/sso/saml.py`) imports it, so the container exited 1 instead of
serving its own CMD. A second latent defect sat in the retired apply route,
which passed no `project_id` and so could never have planned. The operator
carried an inaccurate mental model — "nothing here is live" — for as long as the
declarations said so.

## Detection

**Late, and by a human, during the go-live itself.** Nothing in the repository
caught it because the artifact had no exercising control:

- the image recipe's *only* builder was the **retired** Cloud Run route, so it
  was never built and its dependencies were never imported;
- the declarations (`docs/AGENTCONSOLE-HOSTING.md`, `docs/EDGE-CUTOVER.md`)
  asserted their own non-liveness in prose — a claim no gate read, so the prose
  and the tree could drift apart forever;
- `scripts/check-edge-cutover.sh` existed for the fronting decision but was not
  wired into the gate of record, so the hosting half had no mechanical check at
  all.

This is a **detection gap**: a control that could not fail. The thing that
*should* have caught it is the gate this RCA produced
(`scripts/check-agentconsole-hosting.sh`, GR-12).

## Root cause

The missing control is **"declared ⇒ exercised"**. The repository is rich in
declarations — an overlay, a Dockerfile, a registry, two contract docs — and
each was individually correct. Nothing required that a declared artifact be
*run* or that its declaration be *checked against the tree*. A declare-only
surface is unfalsifiable: it cannot fail, so it is never wrong, so a defect
inside it (an unbootable image) stays invisible while its absence (the go-live
never ran) also stays invisible. The retired route made this worse by removing
the accidental exercise: with no builder, there was not even a build failure to
notice.

## Corrective actions

- `CA-0009` — a binding, self-proving gate: `scripts/check-agentconsole-hosting.sh`,
  registered in `scripts/verify.sh` (issue #1029). It asserts the hosting
  contract offline and mutates one property per rule on every run, so it is a
  gate that can fail — the exact control whose absence is the root cause above.

## Lessons

- `LESSON-0005` — **"declared ≠ exercised"**: a declared artifact (an image
  recipe, an overlay, a contract doc) is unfalsifiable until a control reads it
  or runs it. Give every declaration a gate that can fail, or the declaration
  will drift from the tree silently.

## Evidence

- `docs/AGENTCONSOLE-GOLIVE.md` — the repeatable recipe (this lane).
- `scripts/check-agentconsole-hosting.sh` + its registration in
  `scripts/verify.sh` (this lane, PR for `#1029`).
- The go-live itself: epic `#607`, residual `#946`.

## Follow-up

- **RCA id collision (pre-existing, reported not fixed):** `docs/rca/` carries
  `RCA-0007`/`RCA-0008` *outside* this ledger, while the ledger's next free id
  was also `RCA-0007`. The ledger is canonical (`governance/lessons/README.md`:
  "a learning that is not in this file does not exist"), so this RCA keeps the
  ledger's contiguous id and the `docs/rca/` pair is the one that should be
  renumbered or folded into the ledger. Not fixed here to avoid renumbering
  another lane's artifacts inside this diff.
- The retired GCP route's `apply.yaml` still defaults `_PROJECT_ID: "false"`
  (found during this work) — harmless while the route is retired, but wrong if
  it is ever revived.
