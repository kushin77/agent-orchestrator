# Security Policy

`agent-orchestrator` is a multi-tenant AI-agent-orchestration control plane.
Security is treated as a product guarantee, not an option (see
`docs/GOLDEN-RULES.md` AO-GR-7/AO-GR-15/AO-GR-16).

## Reporting a vulnerability

Please report suspected vulnerabilities **privately** to the repository owner
**kushin77** — do **not** open a public issue for a suspected live
vulnerability or credential.

- **Preferred:** GitHub private vulnerability reporting on this repository
  (Security tab → "Report a vulnerability"), which delivers the report directly
  to the maintainers.
- **Alternative:** contact the owner via the GitHub account `kushin77`.

Please include, where possible:

- The affected repository, file, and version/tag.
- A description of the issue and its impact.
- Steps to reproduce (or a minimal proof of concept).
- Any suggested fix (optional).

## Disclosure process

1. The report is acknowledged and triaged by the owner.
2. The issue is investigated and a fix is prepared (PR against `master`,
   gated by `make verify`).
3. The fix ships in a release per [`RELEASING.md`](RELEASING.md); the reporter
   is notified.
4. Details are disclosed only after a fix is available, unless the issue is
   already public or requires earlier coordinated disclosure.

## Scope

This policy covers the code and configuration in this repository. If you find a
vulnerability in a dependency or in a model-provider/gateway integration,
please report it to the respective upstream maintainer and flag it to us so we
can update our pins.

## Expectations

- No secrets, tokens, or customer data in reports.
- Reports are handled confidentially; no public disclosure before a fix is
  available.
