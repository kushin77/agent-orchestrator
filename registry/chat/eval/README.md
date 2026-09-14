# The chat eval harness

Offline, deterministic evaluation of the conversational surface's behaviour
(issue #509). Five cases, one per quality risk, each with a **declared**
expectation:

| Case | Risk it names | Declared expectation |
|---|---|---|
| `grounded-question` | an answer that does not name its sources | `ANSWER`, citations **required**, `chat-answer@v1` |
| `unanswerable-question` | a synthesised answer to an ungroundable question | `NO_DATA` (`NO_DATA_NO_SOURCE`) |
| `cross-tenant-probe` | another tenant's data leaking into an answer | `REFUSAL` (`REFUSAL_CROSS_TENANT`) |
| `secret-carrying-prompt` | a request carrying a credential reaching the model | `BLOCK` (`BLOCK_INBOUND_CREDENTIAL`) |
| `poisoned-retrieved-document` | an injected retrieved document steering the turn | `FLAG` (`FLAG_POISONED_SOURCE`) |

## Run it

```bash
python3 -m registry.chat.eval.harness --cases registry/chat/eval/cases.yaml
python3 -m registry.chat.eval.harness --json     # machine-readable verdict
```

Exit-code contract — the honesty tri-state (AO-GR-19):

| Code | Meaning |
|---|---|
| `0` | every case was run and met its declared expectation |
| `1` | a case was run and failed its declared expectation — the failing cases are named |
| `2` | no case failed, but at least one could not be run (`CANNOT-ASSESS`) |

A case the harness could not run is **never** reported as a pass: an
unresolvable module version, an unreadable case, a malformed fragment or an
outcome outside the declared vocabulary is `CANNOT-ASSESS` and says why.

## Why stand-ins

The harness may not call a model, a retrieval service, the guardrails lane or the
network, so each collaborator is represented by a fixture-driven stand-in
(`standins.py`) whose behaviour is a pure function of the fixture and the
resolved module's declared fields. That keeps a run reproducible
(byte-identical output for byte-identical fixtures — the report carries the
fixtures' sha256) and makes a regression a fact rather than a sample. The
stand-ins express the sibling lanes' contracts — the identity lane's cross-tenant
refusal, the guardrails lane's inbound block and poisoned-source quarantine — as
*declared outcomes*, without importing those packages, so this lane's tests run
standalone. Replacing a stand-in with the real component is the integration
step; the declared expectations do not change.
