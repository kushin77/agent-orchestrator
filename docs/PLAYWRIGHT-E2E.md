# Playwright E2E module

`vendor/AgenticAutomationFramework` (AAF) is the vendored Playwright E2E
module for this repo, pinned as a git submodule — see `.gitmodules` for the
pin and pin-move procedure. It replaces ad-hoc `pip install playwright` in
lanes: E2E lanes should invoke the suite through its own scripts instead of
standing up their own Playwright install.

Pinned submodule — never edit its contents directly (same rule as
`vendor/CMR`, see `AGENTS.md` "Hard DON'Ts").

## Invoking it

The real entrypoint is `vendor/AgenticAutomationFramework/scripts/portal-e2e.sh`,
which owns the whole server lifecycle (builds the shell, boots the auth-gate,
waits for health, runs Playwright, tears the server down on exit) — the
suite's `playwright.config.ts` deliberately has no `webServer` because this
script injects `BASE_URL`/`JWT_SECRET_KEY` itself:

```bash
cd vendor/AgenticAutomationFramework
bash scripts/portal-e2e.sh
```

Override `E2E_PORT` (default 4180) or `JWT_SECRET_KEY` via env if the lane
needs non-default values. Set `E2E_HARNESS=1` to run against the real
Diagrams backend harness instead of the default offline mode — see
`e2e/DIAGRAMS_HARNESS.md` in the submodule for that path.

The `Makefile` alongside the script also exposes discrete targets
(`install-e2e-deps`, `playwright-install`, `test-e2e`, `verify`) for a lane
that wants to own its own server lifecycle instead.
