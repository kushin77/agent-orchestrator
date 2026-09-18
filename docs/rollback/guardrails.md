# Rollout + rollback — `guardrails`

Path: `guardrails`. Security & guardrails pillar: policy/OPA engine, DLP,
sandbox profiles, chat envelope guardrails, isolation integrity, honesty
attestation. Declared at `faang`, one rung below the 13 `elite` surfaces —
but `faang` also requires the manual `rollback` evidence, so this surface
reports the same `surface-manual-requirement` finding every run and is
covered here for the same reason.

## Rollout

Ships on `master` merge; three dedicated gates: `scripts/check-chat-
guardrails.sh`, `scripts/check-guardrail-controls.sh`,
`scripts/check-guardrail-head-policy.sh`. Sub-surfaces roll out independently
of each other since each owns its own package (`guardrails/policy`,
`guardrails/dlp`, `guardrails/sandbox`, `guardrails/chat`,
`guardrails/isolation`, `guardrails/honesty`), each with its own
`controls.yaml`/`README.md`/tests. `guardrails/policy/startup.py` loads
policy bundles at process start — a bundle change needs a restart to take
effect, not just a merge.

## Detection

- Run the three dedicated gates directly against the live tree.
- `guardrails/policy/engine.py` / `decision.py` — a decision that should have
  been denied and was allowed (or the reverse) is the sharpest
  policy-regression signal; check `guardrails/policy/audit.py`'s trail for
  the actual decisions made, not just the gate's static check.
- `guardrails/dlp/egress.py` / `injection.py` letting through content that
  `scrub-rules.yml` should have caught is a data-exfiltration-risk signal —
  treat as urgent, same class as a secret-policy regression elsewhere.
- `guardrails/sandbox/enablement.py` — a sandbox profile change that under-
  or over-restricts execution shows as either a security gap or legitimate
  work being blocked.
- `guardrails/honesty/analyzer.py` / `attestation.py` — an attestation that
  should fail passing (or the reverse) is the honesty-pillar regression
  signal.

## Rollback

1. **Bad policy bundle** (`guardrails/policy/bundles/`, `startup.py`):
   `git revert <commit>` on `master`, restart the process(es) that load
   bundles at start; re-run `scripts/check-guardrail-controls.sh` and
   `scripts/check-guardrail-head-policy.sh`.
2. **Bad DLP rule** (`guardrails/dlp/scrub-rules.yml` or `engine.py`):
   revert immediately if the regression is under-scrubbing (security-urgent);
   an over-scrubbing regression (false positives blocking legitimate egress)
   can be reverted on the normal PR cadence.
3. **Bad sandbox profile** (`guardrails/sandbox/profiles.yaml`,
   `enablement.py`): revert; confirm via `scripts/check-chat-guardrails.sh`
   that execution is neither over- nor under-restricted after the revert.
4. **Bad chat guardrail** (`guardrails/chat/policy.py`, `verdict.py`):
   revert; re-run `scripts/check-chat-guardrails.sh`.

Because each sub-surface has its own gate and controls file, a rollback here
is scoped to the specific sub-package that regressed — reverting one
guardrail package does not require reverting the others.

Affected: every request/action the affected sub-surface gates — a DLP or
policy-engine regression that under-restricts is a security incident
(urgent, same-day rollback expected); an over-restriction is a availability
regression for legitimate work (normal-cadence rollback acceptable).
