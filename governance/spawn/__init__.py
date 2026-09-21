"""`governance/spawn` — one spawn envelope, one producer (issue #793).

---knowledge---
module_id: governance.spawn
system: governance
app: spawn
solution_class: class
patterns: [provoked-negative-control, fail-closed, lane-isolation, bounded-work]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [produce, render_block]
invariants: ""
gotchas: ""
related: ["#793"]
do_not_duplicate: null
---knowledge---

`governance/**` is a large, well-tested surface — claim ledger, lane isolation,
lifecycle close-out, reconcile, runaway guard, capacity, gate admission — and
none of it was applied **by the act of spawning**. So a local subagent and a
fleet subagent obeyed different rules, and one lane ran 26 gates while every
other lane ran one.

This package fixes that by making the envelope the thing a spawn carries:

* :mod:`governance.spawn.model` — the versioned document and its validation.
  A spawn that cannot present a well-formed envelope is **refused**, by name,
  fail-closed, with its own exit code (``EXIT_REFUSED``).
* :mod:`governance.spawn.sources` — where every field is read from (the claim
  ledger, the isolation identity, the pinned focus, the capacity bound, the gate
  permit, the attempt budget, the issue's own `Verify:` clause).
* :mod:`governance.spawn.render` — the ONE copy of the spawn prose.
  `fleet/terminal.py::build_prompt` consumes it instead of inlining governance.
* :mod:`governance.spawn.liveness` — "a run in flight" is the run marker's own
  evidence (a live child, or a beat the run advanced), never the loop's pid.
* :mod:`governance.spawn.cli` — the same document for a local spawn, with the
  same refusal, so the two regimes converge by construction.

Proven by ``scripts/check-spawn-envelope.sh`` (in ``make verify``): every
required field is provoked missing and refused by NAME, both spawn paths are
driven for real, and the one-gate-per-worktree bound is provoked with a real
second gate.
"""

from __future__ import annotations

from typing import Any, Mapping

from governance.spawn import liveness, model, render, sources

__all__ = [
    "EXIT_CANNOT_ASSESS",
    "EXIT_NOT_OK",
    "EXIT_OK",
    "EXIT_REFUSED",
    "EnvelopeRefused",
    "MARKER",
    "PRODUCER",
    "REQUIRED_FIELDS",
    "Refusal",
    "SCHEMA",
    "VERSION",
    "assemble",
    "collect",
    "dumps",
    "liveness",
    "model",
    "parse",
    "produce",
    "render",
    "render_block",
    "sources",
    "validate",
]

SCHEMA = model.SCHEMA
VERSION = model.VERSION
PRODUCER = model.PRODUCER
REQUIRED_FIELDS = model.REQUIRED_FIELDS

EXIT_OK = model.EXIT_OK
EXIT_NOT_OK = model.EXIT_NOT_OK
EXIT_CANNOT_ASSESS = model.EXIT_CANNOT_ASSESS
EXIT_REFUSED = model.EXIT_REFUSED

Refusal = model.Refusal
EnvelopeRefused = model.EnvelopeRefused
assemble = model.assemble
validate = model.validate
parse = model.parse
dumps = model.dumps

collect = sources.collect
MARKER = render.MARKER


def produce(**sources_kwargs: Any) -> dict[str, Any]:
    """Read every field from its owner, then assemble — or refuse by name.

    This is the one call both spawn paths make. Nothing here invents a value: a
    field that cannot be read is left empty, and :func:`model.assemble` refuses
    the document naming that field.
    """
    fields = sources.collect(**sources_kwargs)
    spawn_meta = fields.pop("spawn", {})
    return model.assemble(fields, spawn=spawn_meta if isinstance(spawn_meta, Mapping) else {})


def render_block(document: Mapping[str, Any], standing: str = "") -> str:
    """The prompt block for an envelope, or the refusal that stops it being used.

    Validated on the way in, deliberately: a consumer that renders an envelope it
    never checked would be the #793 defect again, one layer up. The name is
    ``render_block`` rather than ``render`` so the submodule of that name is not
    shadowed by this function.
    """
    refusals = model.validate(document)
    if refusals:
        raise EnvelopeRefused(refusals)
    return render.render(document, standing)
