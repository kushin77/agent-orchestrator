# RCAs — operational incident writeups

Root-cause analyses for operational incidents (a wedge, a collision, a gate
that stayed quiet when it should have alerted) that are worth a durable
writeup even when they do not yet have board-resolvable issue/PR refs for
the `governance/lessons` ledger (see `governance/lessons/README.md` for the
repo's canonical, ledger-backed RCA pipeline — use it instead of this
directory whenever the incident has real refs to record).

Filename: `docs/rca/<yyyy-mm-dd>-<slug>.md`.

## Index

- [`2026-09-16-pr-queue-clearing.md`](2026-09-16-pr-queue-clearing.md) — the
  zero-byte gate-lock wedge (RCA-0015) and the shared-core-file collision
  class (RCA-0016) from a 17-PR queue-clearing session.
