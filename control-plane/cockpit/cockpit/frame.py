"""The frame composer — deterministic ANSI frames over rendered panels (#566).

The cockpit renders with the same technique ``fleet/console.py`` proves in-tree
(ADR-0026 D5.1): an ANSI palette plus the alternate screen (``\\033[?1049h``).
Every frame is a pure function of (declared functions, fixtures, state), so a
headless test asserts exactly what an operator sees — on the REAL rendered
frame, never on a hand-built dict (the trap this issue names explicitly).

Colour is opt-in per frame: ``enable_color(False)`` is the default, so tests
see plain glyphs and text, exactly like ``fleet/console.py``'s contract.
"""

from __future__ import annotations

from typing import Sequence

#: The console's own frame width (``fleet/console.py`` WIDTH) — the cockpit
#: keeps the same deterministic default; only a real terminal may widen it.
WIDTH = 96

#: The alternate-screen technique, byte-identical to ``fleet/console.py``.
ENTER_SCREEN = "\033[?1049h\033[?25l"
LEAVE_SCREEN = "\033[?25h\033[?1049l"
HOME = "\033[H"
CLEAR_BELOW = "\033[J"

#: The palette — the same escape codes ``fleet/console.py`` uses, so the same
#: facts get the same colours in both surfaces.
ANSI = {
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}
_COLOR = False


def enable_color(on: bool) -> None:
    global _COLOR
    _COLOR = bool(on)


def paint(text: str, code: str) -> str:
    if not _COLOR:
        return text
    return f"{ANSI[code]}{text}{ANSI['reset']}"


def severity_color(severity: str) -> str:
    """The palette colour one severity code carries (the closed RC-2 enum)."""
    if severity == "critical":
        return "red"
    if severity == "warn":
        return "yellow"
    return "green"  # info — an unknown tag reads calm, never as a failure


def rule(title: str, width: int = WIDTH) -> str:
    """A section rule: ``── title ────...``"""
    head = f"\u2500\u2500 {title} "
    if len(head) >= width:
        return head[:width]
    return head + "\u2500" * (width - len(head))


def _fit(line: str, width: int) -> str:
    """Pad or clip one line to exactly ``width`` display cells."""
    if len(line) > width:
        return line[: width - 1] + "\u2026"
    return line + " " * (width - len(line))


def bordered(title: str, body: str, width: int = WIDTH) -> str:
    """One panel tile: a titled box, every line exactly ``width`` cells."""
    inner = max(width - 2, 0)
    label = f" {title} "[: inner - 1]
    top = "\u250c\u2500" + label + "\u2500" * (inner - 1 - len(label)) + "\u2510"
    body_lines = body.splitlines() or [""]
    lines = ["\u2502" + _fit(line, inner) + "\u2502" for line in body_lines]
    bottom = "\u2514" + "\u2500" * inner + "\u2518"
    return "\n".join([top, *lines, bottom])


def _join_row(cells: Sequence[str], cell: int) -> str:
    """One grid row: the cells side by side, shorter ones padded below."""
    cell_lines = [cell_text.splitlines() for cell_text in cells]
    height = max(len(lines) for lines in cell_lines)
    rendered: list[str] = []
    for index in range(height):
        chunks = [
            lines[index] if index < len(lines) else " " * cell
            for lines in cell_lines
        ]
        rendered.append("".join(chunks).rstrip())
    return "\n".join(rendered)


def tile(blocks: Sequence[tuple[str, str]], width: int = WIDTH, columns: int = 2) -> str:
    """Lay bordered (title, body) blocks into a deterministic grid."""
    columns = max(columns, 1)
    cell = max(width // columns, 20)
    rows: list[str] = []
    pending: list[str] = []
    for title, body in blocks:
        pending.append(bordered(title, body, cell))
        if len(pending) == columns:
            rows.append(_join_row(pending, cell))
            pending = []
    if pending:
        rows.append(_join_row(pending, cell))
    return "\n".join(rows)


def compose(
    *,
    title: str,
    meta: Sequence[str],
    panels: Sequence[tuple[str, str]],
    alerts: Sequence[str],
    drill: str,
    command: str,
    status: str,
    width: int = WIDTH,
) -> str:
    """One full cockpit frame: header, tiles, ticker, drill, command line."""
    parts: list[str] = [rule(title, width), *meta]
    parts.append(rule("panels", width))
    parts.append(tile(panels, width, columns=2 if width >= 100 else 1))
    parts.append(rule("alerts", width))
    parts.extend(alerts or ["  NO_DATA  (no alerts are visible — absence is never a green state)"])
    parts.append(rule("drill", width))
    parts.append(drill)
    parts.append("\u2514 " + command)
    if status:
        parts.append("  status  " + status)
    return "\n".join(parts)
