# Provenance — `e2e/erp/` (ERP-10, issue #655)

GR-10 requires a harvested asset to record its source. This lane harvests
**shapes** — the stage discipline of an end-to-end proof — and copies **nothing**.
No upstream file, fixture, schema, text or snippet is present in this directory:
every one of the eight files named at the end of this document is prose or code
written for issue #655.

## The upstream of the module this lane proves

| Field | Value |
|---|---|
| Repository | [`frappe/erpnext`](https://github.com/frappe/erpnext) |
| Licence | GPL-3.0 |
| Mode | **pattern-only** — no upstream code, schema, text or file is copied, translated or vendored |
| Declared by | `integrations/erp/module.yaml` (`provenance:` — `pattern_source_only: true`, `code_copied: false`) and `integrations/erp/core/provenance.json` |
| Read by this lane | **no upstream source.** This lane read no ERPNext file to write these stages; the name appears here because the module declares it as its pattern source |

## Where each pattern this lane uses actually came from

Every pattern below is drawn from **this repository**. The right-hand column names
the code that carries it, so the claim is checkable rather than asserted.

| Harvested shape | In-repo source | How `e2e/erp/` realises it |
|---|---|---|
| A staged end-to-end proof whose evidence is written to disk (`STAGES`, `write_evidence`, `run_golden_path`) | `e2e/golden_path.py` (`STAGES` at line 419), `e2e/wiring.py` (`write_evidence`, line 409) | `golden_path.py` defines the four ERP stages (provision, cycle, scope, metering) and writes each run's evidence through the same `write_evidence` |
| A no-false-green negative control: every refusal is paired with the same measurement coming out the other way | `e2e/negative_controls.py` | `negative_controls.py` pairs each decision — the promoted routes answering 200, the owning tenant's read allowed, the tenant with budget room not stopped — and `tests/test_erp_negative_controls.py` drives each pairing again |
| An offline console session: a key generated locally, the matching JWKS published to the surface's own verifier, a token it really verifies | `e2e/workbook11_portal.py`, `e2e/wiring.py`, `portal/tests/conftest.py` (`FakeAuthGate`) | `gate.py` mints one session the same way, and says why it repeats rather than imports a sibling stage's private helper |
| A lane gate that carries its own mutant AND asserts the mutation landed before judging its output | `scripts/check-erp-ops.sh`, `scripts/check-erp-auth.sh` | `scripts/check-erp-e2e.sh` disables the console's ERP flag gate in a scratch copy, requires the driver to go non-zero naming `feature_disabled`, and refuses to judge a mutation whose anchor count or sha256 did not change |

## The derivations this lane consumes instead of making

The journey this lane runs is **not** an upstream scenario restated here: it is the
merged module's own chain, resolved at run time from this repository's knowledge
index. Where an upstream derivation exists, the lane that made it records it, and
this lane points at that record rather than repeating it — repeating one would
create a second copy of a provenance claim, which is the failure mode GR-10 exists
to prevent.

| Derivation the journey depends on | Recorded by |
|---|---|
| The selling cycle as one document chain (quotation → order → delivery → invoice), stock movement on delivery, GL posting from the invoice, double entry | `integrations/erp/tx/PROVENANCE.md` (ERP-03, #648) |
| The document model, its lifecycle machine and its refusals | `integrations/erp/core/provenance.json` and the ERP-02 lane's schemas |
| The sales families and their SLA/timesheet surfaces | `integrations/erp/crm/PROVENANCE.md` (ERP-05) |
| Procurement and manufacturing | `integrations/erp/ops/PROVENANCE.md` (ERP-04) |
| Metering, the rate card and the budget guard | `integrations/erp/finops/PROVENANCE.md` (ERP-09) |

## What is not copied, and how that is checkable

* this directory holds no upstream file and no upstream fixture — the file list
  below is the whole of it, and every path is new in #655;
* the cycle's four families are not written into the stages as a scenario: the
  chain is *resolved* through `integrations.erp.tx.indexer` and
  `integrations.erp.tx.definitions.load`, and the stage asserts the resolved chain
  equals the acceptance criterion's — so the proof follows the module rather than
  freezing a copy of it;
* the refusals are not restated: `negative_controls.py` imports each sibling
  lane's own `negative_control` driver and each module's exported `REFUSALS`, and a
  lane that adds a refusal without a provocation fails here as well as there;
* the module's flag is not promoted in any file this lane ships: the promoted
  declaration is written into the run's own scratch directory
  (`golden_path.promoted_config`), and `integrations/erp/module.yaml` still ships
  `default: false` (GR-5).

## The files this document covers

```
e2e/erp/__init__.py
e2e/erp/cli.py
e2e/erp/gate.py
e2e/erp/golden_path.py
e2e/erp/negative_controls.py
e2e/erp/tests/conftest.py
e2e/erp/tests/test_erp_golden_path.py
e2e/erp/tests/test_erp_negative_controls.py
```

Plus one additive line in `scripts/pytest-suites.txt` (`e2e/erp`) and the lane's
gate, `scripts/check-erp-e2e.sh`.
