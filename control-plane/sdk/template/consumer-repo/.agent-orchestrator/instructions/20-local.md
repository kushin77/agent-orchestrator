<!-- local: tenant-owned — NOT synced; your repository's own conventions live here. -->

# Local layer — your repository's conventions

This file is **not** synced by the platform (`managed: false` in
`sync.yaml`).  It is the space where your team's own conventions live, above
the governed platform and agent-pack layers.

Suggested local content:

- your repository's branch/merge conventions and code-owner rules;
- local build/test commands and tooling notes;
- team-specific review checklists.

Remember: the platform layer (00) and the agent-pack layer (10) are governed
and auto-synced — do not copy their rules here or try to override them
locally; `make verify` treats a drifted managed layer as a failure.
