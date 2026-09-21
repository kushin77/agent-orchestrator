"""Runtime seam for the sandbox executor.

A runtime is the thing that actually isolates a tool execution. The executor
does not know HOW a profile is enforced: it resolves the security profile and
hands the execution to an injected runtime that enforces it. The default
deployment wires a real Docker runtime (or Firecracker microVM) behind a flag
that ships OFF; tests inject the offline :class:`~sandbox.offline.OfflineRuntime`
model. Every runtime refuses to run while its ``enabled`` flag is False - there
is no code path that executes a tool without an enabled runtime (fail closed).


---knowledge---
module_id: guardrails.sandbox.runtime
system: guardrails
app: sandbox
solution_class: pattern
patterns: [dependency-inversion, persistence-seam, fail-closed]
derives_from: null
owner_sme: security-sme
tier: L1
interfaces: [Runtime]
invariants: "every runtime refuses to run while its enabled flag is False, so there is no path that executes a tool without an enabled runtime"
gotchas: "the executor does not know how a profile is enforced; it hands the execution to an injected runtime"
related: ["#58"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from typing import Protocol

from .model import ExecutionRequest, ExecutionResult, SecurityProfile


class Runtime(Protocol):
    """The injected isolation backend the executor dispatches to.

    ``name`` identifies the runtime in results and audit records; ``enabled``
    is the flag-gated switch (OFF by default for every real runtime); ``run``
    enforces ``profile`` on ``request`` and returns the execution result.
    """

    name: str
    enabled: bool

    def run(
        self, request: ExecutionRequest, profile: SecurityProfile
    ) -> ExecutionResult: ...
