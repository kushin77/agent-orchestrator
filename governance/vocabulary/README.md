# governance/vocabulary — declared fleet vocabularies (issue #777)

Single-authority declarations for names that are load-bearing wire values —
not documentation of a convention, but the actual source a gate checks other
code against for drift.

## Layout

| File | Role |
|---|---|
| [`fleet.yaml`](fleet.yaml) | the fleet's role vocabulary — the closed set of sender/recipient names on the session-fleet envelope (`fleet/schema/message.schema.json`) |

## How the authority is enforced

The vocabulary is declared here and mirrored as constants in
`fleet/channel.py`; `scripts/check-fleet-vocabulary.sh` (wired into
`make verify`) fails the moment the two stop agreeing, so a lane cannot mint a
third name by editing only one side. The same pattern
`scripts/check-finops-chooser.sh` uses for the FinOps vocabulary (#164).

## Related

Issue #777.
