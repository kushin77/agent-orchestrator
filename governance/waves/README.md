# governance/waves — wave-bootstrap delta report (issue #181)

Each wave starts by running a report of what changed in the deepseek and
code-indexing modules since the last wave, plus codeidx context-pack deltas.
The report is a committed **artefact**, not a conversation.

## Layout

| File | Role |
|---|---|
| [`bootstrap.py`](bootstrap.py) | builds the delta report (`build_report`); offline pin-file mode and `--online` `gh api` mode |
| [`model.py`](model.py) | report data model |
| [`ledger.py`](ledger.py) | wave ledger |
| [`cli.py`](cli.py) | operator entry point |
| [`pins.json`](pins.json) | committed offline test-seam baseline — plausible-but-empty, never the real feed |
| [`tests/`](tests/) | bootstrap, ledger and CLI coverage |

## Two feeds

* **Offline mode** (default, deterministic) reads the committed
  `pins.json`. This is a test seam, not the real feed: it exists so the tool
  and the gate can run without network, with a plausible-but-empty baseline
  clearly marked as such.
* **Network mode** (`--online`) queries the deepseek and code-indexing boards
  via `gh api`. Real code, but never run by the gate — only by an operator
  starting a wave.

## Related

Issue #181 (gap 2).
