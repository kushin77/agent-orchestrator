<!-- managed: ao-platform — AUTO-SYNCED governed layer; do not edit locally (make sync overwrites). -->

# Platform layer — governed agent-operating rules

This file is delivered by the agent-orchestrator platform as the **platform
governed layer** of your consumer repo (issue #41, consumer-repo template).
It is auto-synced from your agent-org's governance pack; local edits are
detected by `make verify` and overwritten by `make sync` (born-compliant).

The platform governs the agents embedded in this repo.  These rules are
non-negotiable for any governed agent acting in this repository:

- **Issue-first.** Work is tracked before it is done; a change carries a
  reference to the issue it serves.
- **Verify before done.** A task is done only when its verification gate is
  green and the actual output is reported — never an unverified claim.
- **No secrets.** Credentials/tokens come from the environment or a secret
  manager; nothing is hardcoded, committed, or echoed to logs.
- **Stay in your lane.** The smallest focused change; no unrelated edits, no
  debug leftovers, no unfinished markers.
- **No direct push to the protected branch.** Changes land through review and
  a green gate; merge only work that passes verification.
- **No cross-tenant access.** A session token scopes every call to exactly
  one tenant; there is no fallback to another tenant.
- **Fail closed.** An absent or expired session token, an unknown route, or a
  missing permission is a denial — never a silent pass.

The SDK that enforces these boundaries lives in this repo's declared agent
pack (see the agent-pack layer) and talks to the platform over the gateway /
control-plane / MCP surfaces documented in the SDK contract.
