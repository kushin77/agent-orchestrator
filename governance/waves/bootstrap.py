"""Wave-bootstrap report (issue #181, gap 2): the delta query, code-native.

Each wave starts by running a report of what changed in the deepseek and
code-indexing modules since the last wave, plus the codeidx context-pack
deltas. The report is an **artefact**, not a conversation (sync rule 1).

Two feeds:

* **Offline mode** (default, deterministic) reads the committed pin file
  ``governance/waves/pins.json``. This is a **test seam**, not the real feed:
  it exists so the tool and the gate can run without network. The pins carry a
  plausible-but-empty baseline, clearly marked.
* **Network mode** (``--online``) queries the deepseek and code-indexing boards
  via ``gh api``. It is real code but is **never run by the gate** — only the
  wave operator invokes it, and only with network access.

Determinism: the offline report embeds no wall-clock time; its ``generated_at``
is the pins' own ``as_of``, so two runs over the same pins and ``--since`` are
byte-identical.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from model import PIN_CODEIDX, PIN_DEEPSEEK

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_PINS_PATH = ROOT / "governance" / "waves" / "pins.json"

PINS_SCHEMA = "wave-pins/1"

# Repos the wave consumes (sync rule 2: consume, don't re-implement).
DEEPSEEK_REPO = "kushin77/deepseek"
CODEIDX_REPO = "kushin77/code-indexing"


def load_pins(path: Path | str = DEFAULT_PINS_PATH) -> dict[str, Any]:
    """Load the committed pin file. Raises ValueError when malformed."""
    target = Path(path)
    raw = target.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{target}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{target}: pins must be a JSON object")
    if data.get("schema") != PINS_SCHEMA:
        raise ValueError(f"{target}: unknown pins schema {data.get('schema')!r} (expected {PINS_SCHEMA!r})")
    if not isinstance(data.get("modules"), dict):
        raise ValueError(f"{target}: 'modules' must be an object")
    if not isinstance(data.get("board_deltas"), list):
        raise ValueError(f"{target}: 'board_deltas' must be a list")
    return data


def _module_line(name: str, module: dict) -> str:
    sha = str(module.get("sha", "") or "")
    state = str(module.get("state", "") or "empty-baseline")
    if sha:
        return f"- **{name}** — pinned at `{sha}` ({state})"
    return f"- **{name}** — no pin yet ({state})"


def offline_report(since: str, pins: dict[str, Any]) -> str:
    """Deterministic markdown report from the committed pins (offline test seam)."""
    modules = pins.get("modules", {})
    deepseek = modules.get(PIN_DEEPSEEK, {}) if isinstance(modules, dict) else {}
    codeidx = modules.get(PIN_CODEIDX, {}) if isinstance(modules, dict) else {}
    deltas = pins.get("board_deltas", []) or []
    as_of = str(pins.get("as_of", "") or "")

    lines = [
        "# Wave bootstrap report",
        "",
        "> **Offline mode** — this is a test seam, not the real feed. The live",
        "> report is produced by `bootstrap --online`, which queries the",
        "> deepseek and code-indexing boards and is never run by the gate.",
        "",
        f"- since: `{since}`",
        f"- generated_at: `{as_of}`",
        f"- module: {DEEPSEEK_REPO}",
        f"- module: {CODEIDX_REPO}",
        "",
        "## Module pins (GR-10 provenance)",
        "",
        _module_line(PIN_DEEPSEEK, deepseek),
        _module_line(PIN_CODEIDX, codeidx),
        "",
        "## Board deltas since the last wave",
        "",
    ]
    if not deltas:
        lines.append("_none recorded (empty baseline)_")
    else:
        for delta in deltas:
            if isinstance(delta, dict):
                lines.append(f"- `{delta.get('repo', '')}` #{delta.get('number', '')} — {delta.get('title', '')}")
            else:
                lines.append(f"- {delta}")
    lines.append("")
    return "\n".join(lines)


def fetch_remote_deltas(
    since: str,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> dict[str, Any]:
    """Query the deepseek + code-indexing boards (network). Not run by the gate.

    ``since`` is an ISO timestamp or a commit SHA the boards' issue search
    accepts. Raises RuntimeError when ``gh`` is unavailable or the query fails,
    so the caller can fall back to the offline pins rather than fabricate data.
    """
    run = runner or subprocess.run
    deltas: list[dict[str, Any]] = []
    for repo in (DEEPSEEK_REPO, CODEIDX_REPO):
        result = run(
            [
                "gh", "api",
                f"repos/{repo}/issues",
                "-X", "GET",
                "-f", "state=all",
                "-f", f"since={since}",
                "-f", "per_page=100",
                "--jq",
                ".[] | {repo: \"%s\", number, title, state}" % repo,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"gh api failed for {repo} ({result.returncode}): {result.stderr.strip()}"
            )
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                deltas.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {"since": since, "board_deltas": deltas}


def build_report(
    since: str,
    out: Path | str,
    pins_path: Path | str = DEFAULT_PINS_PATH,
    online: bool = False,
) -> Path:
    """Write the report to ``out``. Offline (deterministic) unless ``online``."""
    if online:
        remote = fetch_remote_deltas(since)
        pins = {
            "schema": PINS_SCHEMA,
            "as_of": remote.get("since", since),
            "modules": {},
            "board_deltas": remote.get("board_deltas", []),
        }
        text = offline_report(since, pins)
    else:
        pins = load_pins(pins_path)
        text = offline_report(since, pins)

    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
