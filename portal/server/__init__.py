"""portal.server — offline control-plane console backend (issue #39).

The portal is a self-contained static console (design-token CSS/JSON twins +
vanilla JS views) served by this stdlib-only Python HTTP layer. The server
implements the console routes (session SSO, RBAC, entity projections) and is
exercised fully offline by pytest against :class:`ConsoleApplication` (no
sockets needed); ``main.py`` wraps it in ``http.server`` for a real serve.

Lane contract: this subtree owns ``portal/**`` only and *consumes* (never
redefines) the frozen vocabularies of the merged pillar lanes:

* ``identity/sso`` (issue #35) - RS256 ``os-session-token`` console SSO.
* ``identity/rbac`` (issue #12) - Org-as-tenant + the platform role pack
  (owner/admin/team-admin/agent-operator) permission vocabulary.
* ``identity/cpapi`` (issue #38) - the control-plane endpoint/approval-gate
  shape this console surface projects.
* ``guardrails/policy`` (issue #26) - the default-OFF controls registry the
  Policies view toggles are bound to.
* ``telemetry/ledger`` (issue #31) - the audit verify-chain record vocabulary.
* ``telemetry/budgets`` (issue #34) + ``registry/*`` (issues #9/#11/#13) -
  the budget/quota and agent/persona/prompt state this console displays.


---knowledge---
module_id: portal.server
system: portal
app: server
solution_class: template
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L0
interfaces: [__version__]
invariants: "the subtree owns portal/** only and consumes the pillar lanes' frozen vocabularies rather than redefining them"
gotchas: ""
related: ["#39"]
do_not_duplicate: null
---knowledge---
"""

__version__ = "1.0.0"
