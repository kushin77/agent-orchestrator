# Operator access — every way into the fleet, and what each one needs

> **Status:** runbook (issue #763). Normative companion:
> [`../fleet/CONTRACT.md`](../fleet/CONTRACT.md) §7.1 declares the steering
> channel the **PRIMARY control plane**; the runbook
> [`../fleet/README.md`](../fleet/README.md) describes the mechanics. This page
> answers one question: **as the operator, which command reaches which surface —
> and from where.**

An operator with no shell on the box used to have no stated path at all. This
page names every surface, gives the exact command for each, and says plainly
which ones need a shell on the box and which do not. Nothing here is invented:
every command below is the one the code implements, and the limits section
records what is **not** reachable today.

## Which do I use?

| I want to… | Surface | Command | Needs |
|---|---|---|---|
| **order work** (the primary control plane) | A2A control channel | `python3 fleet/channel.py order --message <file-or-inline-json>` | shell on the box |
| read the brain's replies | A2A control channel | `python3 fleet/channel.py brain-outbox` | shell on the box |
| see the whole fleet on one screen | live operator view | `make operator` | shell on the box **+ tmux** |
| read state, health, or debug one thing | override terminal (observe) | `python3 fleet/control.py status` / `health` / `debug` | shell on the box |
| steer a running fleet (pause, poke, halt, refresh) | override terminal (steer) | `python3 fleet/control.py <verb>` | shell on the box |
| start / stop the rungs | override terminal (lifecycle) | `python3 fleet/control.py start` / `stop` / `restart` | shell on the box |
| browse the fleet from a browser, from anywhere | browser console | `make console` | shell on the box to start it; a browser to use it |
| **control** the fleet from outside the box | — | **not reachable today** (see [limits](#what-an-operator-with-no-shell-on-the-box-can-and-cannot-do)) | — |

## 1. The A2A control channel — the PRIMARY control plane

The steering channel is the fleet's primary control plane, not a fallback:
`fleet/CONTRACT.md` §7.1 states it normatively, `fleet/directive.json` carries it
into every subagent prompt, and
[`../scripts/check-fleet-contract.sh`](../scripts/check-fleet-contract.sh) — in
`make verify` — fails by name when the declaration is removed. The transport is
the file mailbox of `fleet/channel.py`; the chain is
`operator → brain → sister → subagent`, and the transport refuses a skip.

The operator **orders the brain** — it never addresses the sister directly
(`order` is the only way in; `send` refuses an operator sender):

```bash
# 1. order work — the order is a directive the brain turns into a dispatch
python3 fleet/channel.py order --message '{
  "type": "directive",
  "task": {"issue": 763, "lane": "fleet"},
  "body": "dispatch one subagent for #763 and report the evidence"
}'

# --message takes inline JSON or a path to a JSON file:
cat > /tmp/order-763.json <<'JSON'
{"type": "directive", "task": {"issue": 763, "lane": "fleet"}, "body": "go"}
JSON
python3 fleet/channel.py order --message /tmp/order-763.json

# 2. read the brain's replies (acks and refusals), newest last
python3 fleet/channel.py brain-outbox
python3 fleet/channel.py brain-outbox --limit 5

# 3. watch the brain's own inbox — the oldest order still waiting for it
python3 fleet/channel.py brain-inbox --timeout-seconds 5    # exits 1 (IDLE) if none
```

A worked example of ordering work, end to end:

1. `order` writes the order into the brain's inbox and prints its message id.
   The message is validated first: a bad tier/thinking, an unknown type, or a
   replayed `nonce` is **refused** before it moves.
2. The brain (`bash fleet/brain.sh`) drains it with `brain-inbox`, signs a
   directive for the sister, and reports what it did.
3. `brain-outbox` is where the operator reads that answer — including a refusal,
   which names the contract rule it enforced.

Two properties that matter when ordering work:

- **A directive is the only authorisation for work** (§7.1 rule 1). An order is
  how an operator starts work; a comment or a verbal hand-off is not.
- **Superseding is explicit.** A new directive supersedes an earlier one for the
  same issue by its `supersedes` field or id — never by silent overwrite, so the
  order of orders is recorded in artifacts rather than reconstructed.

## 2. The human-override terminal — `python3 fleet/control.py <verb>`

The override terminal is the operator's direct lever on the rungs. It has 18
verbs; `python3 fleet/control.py --help` lists them, and
`docs/REMOTE-CONTROL-GAP-ANALYSIS.md` §2.1 inventories each one's reach.

**Observe** (read-only — no state is changed):

| Verb | What you get |
|---|---|
| `status` | rungs, pause/stop flags, tracked runs |
| `health [--stale-minutes N]` | tri-state signal: 0 healthy / 1 degraded / 2 failing |
| `debug [--tail N]` | a full, non-destructive state dump |
| `watch` | idle-watch the audit stream (`.fleet/slog.jsonl`) |

**Steer** (change what a running fleet is doing):

| Verb | What it does |
|---|---|
| `poke` | ping the sister; it acks (liveness, without stopping it) |
| `pause` / `resume` | hold the queue / release it (an in-flight run finishes) |
| `override --issue N` | force `#N` past a live claim |
| `refresh` | `git pull --ff-only` + board snapshot + `make verify` |
| `update` | `refresh` + rebuild the knowledge index |
| `halt` | stop the fleet |

**Lifecycle** (start/stop the rungs on this host):

| Verb | What it does |
|---|---|
| `start` | start whichever rungs are missing |
| `stop` | exit after the current run, cleanly |
| `kill` | terminate the run, release its claim, escalate |
| `restart` | re-exec the same code (`SIGTERM` then re-exec) |
| `cron <sub>` | install/status/run/respawn/disable/enable/uninstall the cron entry |
| `live` / `attach [--dry-run]` | ensure the rungs, then `tmux attach -t fleet` |

`live` and `attach` are the same verb by two names, and both need **a shell on
the box and `tmux`**: the session is a *view* over rungs that run detached, and
without `tmux` the verb reports that it cannot host the view (exit 1) instead of
pretending it did.

## 3. The live operator view — `make operator`

```bash
make operator              # report the surfaces, then start/attach the live view
bash fleet/run-fleet.sh    # the same thing, one line shorter
python3 fleet/control.py live --dry-run   # print the tmux commands; build nothing
```

`make operator` prints the surfaces above (so the way in is discoverable from the
command itself) and then delegates to `fleet/run-fleet.sh`, which is
`python3 fleet/control.py live` — the tmux layout has exactly one definition
(`control.live_layout()`), and this target does not reimplement it.

**It fails loudly.** If the box cannot host the view (no `tmux`), it exits
non-zero and names the reason, then points at the same content without tmux:

```bash
python3 fleet/console.py         # the self-refreshing single-pane dashboard
python3 fleet/console.py --once  # one frame, for a script or a log
```

`make operator` never prints a success it cannot evidence.

## 4. The browser console (remote-capable) — `make console`

```bash
make console                                    # 127.0.0.1:8787 (the default)
make console CONSOLE_HOST=0.0.0.0 CONSOLE_PORT=8787
python3 -m portal.server.main --host 127.0.0.1 --port 8787   # the real command
```

`make console` starts the real server (`python3 -m portal.server.main`, backed by
`portal/server/httpd.py::serve`). It prints the line it is serving:

```
agent-orchestrator console listening on http://127.0.0.1:8787 (static root: ...)
```

Two facts decide whether this surface is safe, and both are properties of the
server rather than of this document:

1. **It binds loopback by default** (`127.0.0.1:8787`). Reaching it from
   elsewhere therefore requires deliberately binding a reachable interface
   (`--host`), which is a decision an operator has to make, not a default.
2. **It has no login of its own.** It redirects an unauthenticated visitor to
   the shared auth gate and establishes a console session only from a verified
   auth-gate RS256 `os-session-token`. With no JWKS mirror configured it
   **trusts no key and refuses every session — it fails closed**.

The two environment variables that configure it:

```bash
PORTAL_AUTH_GATE_JWKS_FILE=/etc/ao/auth-gate-jwks.json \
ROOT_ADMIN_EMAILS=root@platform.example.com \
  make console CONSOLE_HOST=0.0.0.0
```

- `PORTAL_AUTH_GATE_JWKS_FILE` — a mounted mirror of the auth gate's published
  JWKS (`GET /auth/.well-known/jwks.json`). **Without it, no session is
  accepted.** (The inline `PORTAL_AUTH_GATE_JWKS` payload is the alternative; a
  malformed mirror raises at boot rather than degrading to an empty trust set.)
- `ROOT_ADMIN_EMAILS` — the comma-separated allowlist that decides super-admin;
  the token never decides it.

**The security requirement, plainly:** never expose this console on a public
interface without the auth gate in front of it, and never rely on the bind
address as a control. A tunnel or reverse proxy in front of the console is a
**new infrastructure surface**: it is declared in code, ships **flag-gated OFF**
(GR-5), and lands as a reviewed change — never a console click and never an
ad-hoc `terraform apply`.

## 5. What an operator with no shell on the box can and cannot do

Honest limits, measured (`docs/REMOTE-CONTROL-GAP-ANALYSIS.md` §2.4/§2.6):

- **Read: yes, from a browser — once the console is reachable and the auth gate
  is in front of it.** The console's fleet-relevant families are GET-only and
  its projection is `fleet/console.py`'s snapshot; with no shell on the box, the
  console has to be started by someone who has one.
- **Control: not today.** No verb is reachable over a network: the transport is
  the local file mailbox plus local signals (ADR-0011), the console refuses
  every fleet mutation on that path, and there is no caller identity on the
  control path to authenticate. Stated as a surface, not as a promise.
- **The declared graduation path** is the remote control API
  (`surfaces.remote_control`, RC-3 of EPIC #551, issue #554): the
  operator→fleet command channel on the existing console app
  (`POST /api/control/<family>/<action>`), which consumes RC-2's closed verb
  declaration and re-implements no control action. It ships **flag-gated OFF**
  and the flag is checked **before** authentication, so while it is off the
  whole family answers `404 feature_disabled` — an unpromoted surface is absent,
  never silently available. Check the registry for the current state:

  ```bash
  grep -n -A 3 'remote_control:' infra/feature-flags/registry.yaml
  make feature-flags
  ```

So: the fleet is **ordered** from a shell on the box (the primary control plane,
§1), **watched** from a shell (`make operator`, §3) or a browser (`make console`,
§4), and **steered** from the override terminal (§2). A remote operator's path is
the console — read-only until the remote control API is promoted.

## See also

- [`../fleet/CONTRACT.md`](../fleet/CONTRACT.md) §7.1 — A2A is the PRIMARY
  control plane (normative; the invariants).
- [`../fleet/README.md`](../fleet/README.md) — the runbook: bootstrap, mailbox
  layout, listener loop, day-to-day commands.
- [`REMOTE-CONTROL-GAP-ANALYSIS.md`](REMOTE-CONTROL-GAP-ANALYSIS.md) — the
  measured inventory of every control surface, and the remote-control EPIC.
- [`../docs/decision-records/ADR-0011-session-fleet-transport.md`](decision-records/ADR-0011-session-fleet-transport.md)
  — why the transport is what it is.
