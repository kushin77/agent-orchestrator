"""Public API + proxy allowlist boundary - authn never authz (issue #37).

The outermost edge of the platform: it authenticates callers (consuming
issue #35 ``verify_session`` semantics via an injected verifier seam), applies
an explicit ALLOWLIST of publicly reachable routes/methods (path allowlist +
method allowlist, fail closed, path-traversal safe), and forwards the verified
identity + role snapshot downstream WITHOUT deciding authorization
(authn never authz - authorization happens inside after scope resolution, in
the #38 REST server composing ``identity/rbac`` issue #12).

This subtree is ``identity/edges/**`` only (one issue = one lane).  Import as
``identity.edges`` (``identity/`` is a PEP-420 namespace package, mirroring
``identity/sso``/``identity/rbac``).  Everything is offline - the boundary is
a gateway/proxy abstraction + allowlist model, testable without a real HTTP
server (the real REST server arrives in issue #38).

Public surface:

- ``model`` - edge vocab: routes, forwarded identity/request, versioned
  envelope + closed error codes.
- ``paths`` - path-traversal guard + route-template matching (fail closed).
- ``allowlist`` - the explicit public route allowlist + matcher + default.
- ``authn`` - the injected session-verifier seam (authN; no authz).
- ``forward`` - build-don't-copy outbound request builder.
- ``envelope`` - versioned, envelope-standardized responses.
- ``edge`` - ``PublicEdge`` orchestrator (authN + allowlist + forward).


---knowledge---
module_id: identity.edges
system: identity
app: edges
solution_class: class
patterns: [package-contract, public-surface, authn-never-authz]
derives_from: null
owner_sme: security-sme
tier: L0
interfaces: [model, paths, allowlist, authn, forward, envelope, edge]
invariants: ""
gotchas: ""
related: ["#37"]
do_not_duplicate: null
---knowledge---
"""

from identity.edges import (
    allowlist,
    authn,
    edge,
    envelope,
    forward,
    model,
    paths,
)

__all__ = [
    "allowlist",
    "authn",
    "edge",
    "envelope",
    "forward",
    "model",
    "paths",
]
