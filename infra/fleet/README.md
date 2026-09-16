# infra/fleet — the fleet-cron container image

Owner lane: **infra** (issue #709, EPIC #706 D1). See
[`../../AGENTS.md`](../../AGENTS.md) and
[`../../docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md).

## Purpose

One image that runs the fleet's scheduled automation off the box it was written
on: the same three crontab lines, the same three rungs, the same state roots,
the same binaries — declared instead of assumed.

The porting contract is [`inventory.yaml`](inventory.yaml), and it is enforced,
not documented: [`../../scripts/check-fleet-cron-image.sh`](../../scripts/check-fleet-cron-image.sh)
(`make verify`) compares the image against it in both directions and re-measures
the schedule, the rungs and the state roots from the modules that own them.

```
infra/fleet/
  README.md        this file
  inventory.yaml   the dependency inventory — the porting contract
  Dockerfile       python:3.12-slim + tini + git / gh / openssh-client + claude
  entrypoint.sh    asserts the schedule from fleet/cron.py, then runs
```

## Dev-first

The image builds on this box, and that is a criterion rather than a claim:

```bash
docker build -f infra/fleet/Dockerfile -t agent-fleet-cron:dev .
docker run --rm agent-fleet-cron:dev python3 fleet/cron.py status
```

Every layer that reaches the network sits **above** `COPY . /repo`, so the
dependency layers are cache-stable across a checkout edit: the first build pays
for `apt`, `gh` and the pinned CLAUDE release once, and every later build of an
edited checkout rebuilds only the copy layer.

### The one thing this host does to a cold build, measured

A COLD build on this box needs the host's name resolution borrowed
(`docker build --network=host`), and the reason is the host, not the image: a
container on the default bridge network here gets `systemd-resolved`'s
`127.0.0.53` stub in `/etc/resolv.conf`, which resolves nothing outside the host
namespace, so the `apt`/`curl` layers stall or die rather than fail cleanly.

```
$ docker run --rm python:3.12-slim python3 -c "import socket; socket.gethostbyname('deb.debian.org')"
socket.gaierror: [Errno -3] Temporary failure in name resolution
$ docker run --rm --network=host python:3.12-slim python3 -c "import socket; print(socket.gethostbyname('deb.debian.org'))"
151.101.2.132
```

Measured 2026-09-16: the cold build completed with `--network=host`, and the
issue's own command then ran **unchanged and cached** — eleven `Using cache`
layers, `rc 0`, and `cron: installed (3 line(s))`. So the image builds on this
box either way; only the first, network-touching build on a host whose
containers cannot resolve has to say so. `scripts/check-fleet-cron-image.sh`
probes exactly this and prints which of the two it used, so the environment
defect stays visible instead of being papered over — with working container DNS
the plain command builds the same image.

## The schedule has one owner, and it is not this directory

`fleet/cron.py` owns the three crontab lines — their markers, their interval,
their log paths and their interpreter path. The image holds **no second copy** of
any of it: `entrypoint.sh` calls `python3 fleet/cron.py install`, and
`.dockerignore`-style copying of a crontab into the image is refused by name in
the gate. A schedule that exists twice is a schedule where one copy is wrong and
nobody knows which.

```mermaid
flowchart TD
    E["entrypoint.sh"] -->|"python3 fleet/cron.py install"| C["fleet/cron.py<br/>(the ONE owner)"]
    C -->|"three marked lines"| CT["crontab"]
    CT --> W["watchdog.py run<br/>ao-fleet-watchdog"]
    CT --> P["prune.py run --apply<br/>ao-fleet-prune"]
    CT --> R["reconcile/cli.py watch<br/>ao-fleet-reconcile"]
    E -->|"no command given"| F["cron -f<br/>(foreground)"]
    E -->|"command given"| X["exec the command"]
```

`install_lines()` refreshes only the lines carrying the fleet's own markers and
keeps every foreign line, so asserting the schedule on each start is idempotent.
`AO_FLEET_CRON_NO_INSTALL=1` skips the assertion — the gate runs the issue's own
`status` command with and without it and requires the two to disagree, which is
what makes "the entrypoint is what installs the schedule" a measurement.

## What the image deliberately does NOT contain

`.board/` and `.fleet/` are runtime **state** — a claim ledger, a mailbox, a
heartbeat — and they are excluded from the build context on purpose. An image
that baked one host's board would hand every container the same stale claims, so
the image creates the paths empty and they are the mount points a real run fills.
The inventory records that the image *depends* on the exclusion, and the gate
checks it as a subset of `.dockerignore`: another image may exclude more, never
less.

No secret is baked in. The image carries no `.env`, no key and no token; the
gate refuses a Dockerfile that names one, and the only credential the fleet uses
at run time is `gh`'s own, mounted in.

## Flag posture (GR-5)

The image is a build artifact, not a deployed surface: `infra/fleet/` declares no
Terraform resource and no service flag, so it ships nothing ON. Deployment of the
image onto a scheduler is D2–D7's work; that is where the OFF-by-default flag
belongs, alongside the resource it gates.

## What D2–D7 inherit from here

* **The inventory is the porting contract.** A new dependency is a PR against
  `inventory.yaml` and the Dockerfile together — the gate refuses a package that
  is in one and not the other.
* **The interpreter path is load-bearing.** `fleet/cron.py` writes its lines with
  the literal `/usr/bin/python3`; this image provides that path (a symlink to the
  image's own python) rather than editing the schedule's owner. A port that drops
  the symlink gets ticks that die with a 127 nobody reads.
* **`cron`, `tini` and `openssh-client` are derived, not decorative.** Each has a
  provoked control in the gate that removes it from a one-line derived image and
  requires the result to be reported broken.

## Dev-first: the dry run (D2, issue #710)

D1 built the image. D2 starts it **here**, on the box the schedule was written
on, with every job in dry-run — because the first porting step must not be an
experiment on production: `.fleet/` is the fleet's mailbox, heartbeat and audit
rail, and `.board/` is the claim ledger every other lane reads.

```bash
docker compose -f infra/fleet/docker-compose.agent-cron.yml up -d
curl -fsS http://localhost:8790/healthz
docker compose -f infra/fleet/docker-compose.agent-cron.yml logs --no-color | grep -c 'dry-run'
docker compose -f infra/fleet/docker-compose.agent-cron.yml down
```

`-f` is on every line on purpose: this repository has no default
`docker-compose.yml` at its root, so a bare `docker compose logs` resolves a file
that does not exist. The three commands above are the ones the gate runs.

```
infra/fleet/
  env_contract.py                  the ONE declaration of the image's environment
  dev_run.py                       the dry-run dispatch: what runs in the container
  healthz.py                       the /healthz surface, answered from the decision
  docker-compose.agent-cron.yml    dev-first: the image, its state mounts, its port
  (D1, unchanged in spirit)
  Dockerfile · entrypoint.sh · inventory.yaml · README.md
```

### Four measurements, not four claims

**1. The schedule is read from its owner, and a role table that drifts fails.**
`dev_run.py` asks `fleet/cron.py` for `MARKERS` — there is no second list of the
jobs — and REFUSES (`role-table-drift`) when its own role table does not cover
them exactly, so a fourth scheduled job with no declared dry-run form fails here
instead of being silently skipped.

**2. A dispatch cannot carry `--apply`.** Every argv is checked and the token is
refused by name (`apply-in-dispatch`); the environment cannot turn it on either,
because `env_contract.py` refuses any `AO_FLEET_DRY_RUN` other than `1`
(`dry-run-required`). The watchdog is not dispatched at all, and the decision
document says so *with its reason*: `fleet/watchdog.py`'s pass spawns the rungs,
so a dry-run of it is a contradiction rather than a flag — which keeps the third
job present in the evidence instead of quietly absent.

**3. The state is checksummed before and after, and WHO is blamed is decided by
the measured mount.** Each root's flags are read from `/proc/self/mountinfo` on
every run, so "read-only" is in the evidence rather than in this sentence:

* a root mounted **read-only** cannot be written by the container at all, so a
  change under it is a **concurrent writer** — the live fleet is still running on
  this box, and its own heartbeat must never read as "the dry run applied";
* a root mounted **writable** has this container as its only writer, so a change
  at a path the dispatched roles may write **fails the run**, naming the file.

The permitted set is read from `fleet/prune.py` (`PRUNABLE_DIRS` + `LOGS`), the
module that owns the retention policy — never restated here. Its scope is stated
as what it is: it is exact for the retention rail, the role whose *scheduled*
form carries `--apply`; the reconcile rail is covered structurally, by the
read-only mount measured per root.

**4. The stop is clean, and the probe can fail.** The SIGTERM/SIGINT handler is
installed *before* the first role runs, so `docker compose down` is a handled
stop and `docker inspect` records exit code 0 — an operator can tell it apart
from a crash. `/healthz` answers **200** only for a run whose verdict is `ok`
and which left the permitted paths alone; a run that wrote answers **503** with
the file it moved, and any other path is a 404. Both are provoked in the gate: a
one-line mutant image whose prune role carries `--apply` (with the argv check
disabled, so only the state guard can catch it) must be reported broken, naming
the file, and its `/healthz` must answer 503.

### Where the state comes from

The compose file mounts the two state roots of **the checkout it lives in**,
read-only, and `create_host_path: false` means a missing root is an error rather
than a silently created, empty, root-owned directory. Point it at another
checkout when the state lives elsewhere — a lane worktree reading the shared
one, or the gate's snapshot of the live state:

```bash
AO_FLEET_HOST_REPO=/home/akushnir/agent-orchestrator \
  docker compose -f infra/fleet/docker-compose.agent-cron.yml up -d
```

`create_host_path: false` (and not the default) is the deliberate choice: with
the default, a checkout that has no `.fleet/` yet gets one created as root, empty,
and the run then "passes" against a board that is not the board.

### What D3+ inherits from here

* **The environment contract is the seam.** A new variable is a line in
  `env_contract.py` and nothing else; the entrypoint and the harness both read
  that one declaration.
* **The dry-run refusal is deliberate, not incidental.** `dry-run-required`
  refuses `AO_FLEET_DRY_RUN=0` because *this* lane is the dry-run harness. A lane
  that adds an applying mode must edit that rule — and the gate's provocation for
  it will make the edit visible instead of silent.
* **The role table is keyed by the schedule's own markers.** A new job in
  `fleet/cron.py` without a dry-run form fails this gate, by design.

## Provenance

The image was written for this repository against the issue's own inventory; no
file was copied from anywhere. Four *patterns* were adopted after reading the
sibling fleet's images (GR-10 — the source is recorded because the shape is not
invented here):
| Pattern | Source (repo · path) | Note |
|---------|----------------------|------|
| `tini` as PID 1 in front of an `entrypoint.sh`, and a dependency-pinning posture (`ARG <TOOL>_VERSION=<exact>`) | `kushin77/leaderboard` · `docker/worker-fleet/Dockerfile` | the sibling image runs `/usr/bin/tini -- /entrypoint.sh`; this one pins the CLAUDE release and verifies its checksum instead of pinning apt versions, because the release manifest carries one |
| the schedule as the image's job, run in the FOREGROUND | `kushin77/leaderboard` · `docker/worker-fleet/` (`cron-sidecar` role) | a daemonising main process makes the container exit and take the schedule with it |
| repo content vs. runtime state as separate concerns (state is mounted, never baked) | `kushin77/leaderboard` · `docker/worker-fleet/Dockerfile` (bind-mount over `/repo`, `immutable source tree` layers) | this image `COPY`s the checkout (the issue asks for it) and mounts only the state roots |

