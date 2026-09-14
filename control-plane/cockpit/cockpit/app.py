"""The interactive cockpit — state, the keyboard loop, and one-shot commands.

The app composes its frame from the pieces below, and owns nothing that the
modules it consumes own:

* the workspace is RC-10's role recommendation (a LENS — additive filtering,
  narrowing-only; ADR-0026 D8/D9) — selecting a function outside the lens is
  refused by name (``role_lens``), never silently widened;
* panels are rendered by ``cockpit_render.render`` (RC-10) — a function id the
  registry does not declare is refused BY NAME (``UNDECLARED-FUNCTION``), which
  is the registry-conformance gate's provoked mutant;
* commands are resolved by ``cockpit_registry.resolve_call`` and delivered by
  the RC-5 client half (``cockpit.plane``) — one receipt or one named refusal
  per action;
* the drill and the ticker are modules of this package (``drill``, ``ticker``).

The keyboard language is ONLY declared mnemonics plus the fixed cockpit keys.
No ad-hoc verb exists anywhere in this package.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, TextIO

from . import drill as drill_module, flags as flags_module, frame
from . import plane as plane_module, registry as registry_io, ticker as ticker_module

ROLES: tuple[str, ...] = ("CTO", "VP-Eng", "Manager", "Analyst")

#: The fixed cockpit keys. Everything else the command line accepts must be a
#: declared RC-10 mnemonic.
WORKSPACE_KEYS = {"c": "CTO", "v": "VP-Eng", "m": "Manager", "a": "Analyst"}

COCKPIT_KEYS = {
    "?": "help",
    "h": "help",
    "q": "quit",
    "d": "drill down",
    "b": "drill back up",
    "k": "acknowledge the selected alert",
    **{key: f"workspace {role}" for key, role in WORKSPACE_KEYS.items()},
}

HELP_TEXT = "\n".join(
    (
        "  cockpit keys — the command line accepts ONLY declared mnemonics plus these:",
        "  ? / h    this help",
        "  q        quit",
        "  c/v/m/a  workspace: CTO / VP-Eng / Manager / Analyst (lenses, never permissions)",
        "  d        drill down (org \u2192 lane \u2192 issue \u2192 agent \u2192 call \u2192 tool-call)",
        "  b        drill back up",
        "  1-9      select the drill row before d",
        "  k        acknowledge the selected alert (the declared channel.send, audited)",
        "  <MNEMONIC> [name=value ...]  one declared RC-10 function;",
        "            an irreversible command needs confirm=<MNEMONIC> at the end",
    )
)


def _cockpit_refusal(code: str, reason: str) -> str:
    """One cockpit-level named refusal, rendered with the RC-5 client's shape."""
    from aoctl.refusals import EXIT_REFUSED, Refusal

    return Refusal(code=code, reason=reason, exit_code=EXIT_REFUSED).render()


@dataclass
class Cockpit:
    registry: Any
    fixtures: Mapping[str, Any]
    plane: plane_module.CockpitPlane
    flag_state: Callable[[str], str]
    role: str = "Analyst"
    session: str = ""
    width: int = frame.WIDTH
    source: Optional[drill_module.DrillSource] = None
    alerts: list[ticker_module.Alert] = field(default_factory=list)
    acked: dict[str, str] = field(default_factory=dict)
    drill: Optional[drill_module.Drill] = None
    selected_alert: int = 0
    status: str = ""
    quit: bool = False

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"{self.role!r} is not a declared role: {list(ROLES)}")
        if self.source is not None and self.drill is None:
            level_functions = getattr(self.source, "level_functions", None)
            if not level_functions:
                level_functions = {level: level for level in drill_module.LEVELS}
            self.drill = drill_module.Drill(
                source=self.source, level_functions=dict(level_functions)
            )
            if not self.drill.path[0][1]:
                self.drill.path[0] = ("org", self.source.root_title())

    # -- the workspace lens -------------------------------------------------
    def workspace_ids(self) -> list[str]:
        """The role's recommended functions — a lens, never a permission set."""
        return registry_io.workspace_ids(self.registry, self.role)

    def in_lens(self, function_id: str) -> bool:
        return function_id in self.workspace_ids()

    # -- panels -------------------------------------------------------------
    def panel_frame(self, function_id: str, fixture: Any) -> str:
        """One panel frame, rendered by RC-10's renderer, checked by name.

        An undeclared function id renders a named failure — never a frame.
        A function whose own surface flag is off renders the ``disabled``
        condition naming that flag (ADR-0026 D10.3).
        """
        function = self.registry.functions.get(function_id)
        _, render_module = registry_io.modules()
        if function is None:
            return "\n".join(
                (
                    f"  FAILED  UNDECLARED-FUNCTION: {function_id}",
                    "  a rendered panel names a function the registry does not declare",
                )
            )
        off = next(
            (
                flag
                for flag in function.flags
                if self.flag_state(flag[len("surfaces."):]) != "on"
            ),
            None,
        )
        if off:
            fixture = render_module.Fixture(
                function_id=function.id, outcome="disabled", flag=off
            )
        elif fixture is None:
            fixture = render_module.Fixture(
                function_id=function.id,
                outcome="error",
                reason=f"fixtures/bodies.json declares no fixture for {function.id}",
            )
        return render_module.render(function, fixture)

    def compose(self) -> str:
        """One full frame: header, role tiles, ticker, drill, command line."""
        surfaces = [
            f"surfaces.cockpit {self.flag_state(flags_module.SURFACE)}",
            f"session {'present' if self.session else 'absent'}",
            f"plane {self.plane.base_url}",
        ]
        meta = [f"  cockpit \u2014 role {self.role:<8} " + " · ".join(surfaces)]
        panels = [
            (function_id, self.panel_frame(function_id, self.fixtures.get(function_id)))
            for function_id in self.workspace_ids()
        ]
        alerts = ticker_module.render(self.alerts, self.acked, self.width).splitlines()
        drill = self.drill.render(self.registry, self.width) if self.drill else (
            "  NO_DATA  (no drill source \u2014 absence, never a green state)"
        )
        command = (
            f"{self.role} \u203a  ? help · q quit · c/v/m/a workspace · "
            "d drill · b back · k ack · 1-9 select \u2014 type a declared mnemonic"
        )
        return frame.compose(
            title="cockpit \u2014 the terminal cockpit (RC-11)",
            meta=meta,
            panels=panels,
            alerts=alerts,
            drill=drill,
            command=command,
            status=self.status,
            width=self.width,
        )

    # -- the keyboard -------------------------------------------------------
    def handle_line(self, line: str) -> str:
        """One command line: a cockpit key, or one declared mnemonic."""
        line = line.strip()
        if not line:
            return ""
        token, _, rest = line.partition(" ")
        rest = rest.strip()
        if token in COCKPIT_KEYS or (token.isdigit() and len(token) == 1):
            return self._handle_key(token)
        if token in self.registry.functions:
            return self._handle_mnemonic(token, rest)
        return f"REFUSED  UNKNOWN-FUNCTION: {token!r} is not a declared cockpit function"

    def _handle_key(self, key: str) -> str:
        if key in ("?", "h"):
            return HELP_TEXT
        if key == "q":
            self.quit = True
            return "quit requested"
        if key in WORKSPACE_KEYS:
            self.role = WORKSPACE_KEYS[key]
            return f"workspace {self.role} (a lens over the same function set)"
        if self.drill is None:
            return "REFUSED  drill: no drill source is attached"
        if key == "d":
            if self.drill.descend():
                return f"drill {self.drill.breadcrumb()}"
            return "REFUSED  drill: this level has no rows to descend into"
        if key == "b":
            if self.drill.ascend():
                return f"drill {self.drill.breadcrumb()}"
            return "REFUSED  drill: already at the top level"
        if key.isdigit():
            rows = self.drill.rows()
            index = int(key) - 1
            if 0 <= index < len(rows):
                self.drill.selected = index
                return f"selected {index + 1}: {rows[index].title}"
            return f"REFUSED  drill: row {key} does not exist (1-{len(rows)})"
        if key == "k":
            return self._acknowledge_selected()
        return f"REFUSED  UNKNOWN-KEY: {key!r} is not a cockpit key"

    def _handle_mnemonic(self, function_id: str, rest: str) -> str:
        if not self.in_lens(function_id):
            return _cockpit_refusal(
                "role_lens",
                f"{function_id} is not in the {self.role} workspace \u2014 "
                "a role lens narrows, it never widens",
            )
        confirm = ""
        tokens: list[str] = []
        for token in rest.split():
            if token.startswith("confirm="):
                confirm = token.partition("=")[2]
                continue
            tokens.append(token)
        given, errors = self._coerce_pairs(function_id, tokens)
        if errors:
            return "REFUSED\n" + "\n".join(f"  {error}" for error in errors)
        return self._dispatch(function_id, given, confirm=confirm)

    def _dispatch(
        self, function_id: str, given: Mapping[str, Any], *, confirm: str = ""
    ) -> str:
        """Resolve and send one declared function (the interactive send)."""
        from aoctl.refusals import Refusal, local_refusal

        call, findings = registry_io.resolve(function_id, given)
        if findings:
            return "REFUSED\n" + "\n".join(
                f"  FAIL  {finding.code}: {finding.detail}" for finding in findings
            )
        function = self.registry.functions[function_id]
        request = self.plane.request_for(call, plane_module.mint_command_id())
        if function.effect_class == "irreversible" and confirm != function_id:
            return local_refusal(
                "confirmation_required",
                f"{function_id} declares effect_class {function.effect_class!r}; "
                f"end the line with confirm={function_id}",
            ).render()
        if not self.session:
            return local_refusal(
                "no_session",
                "no console session token was supplied, and the control family's "
                "only caller identity is the console session (ADR-0025 D2)",
            ).render()
        try:
            record = self.plane.send(request, session=self.session)
        except Refusal as refusal:
            return refusal.render()
        from aoctl.receipt import render as render_receipt

        return render_receipt(record, verb=function_id)

    def _acknowledge_selected(self) -> str:
        """Ack the selected alert through the DECLARED command, via the API."""
        if not self.alerts:
            return "REFUSED  ticker: there are no alerts to acknowledge"
        index = min(max(self.selected_alert, 0), len(self.alerts) - 1)
        alert = self.alerts[index]
        function_id, given = ticker_module.ack_call(alert)
        if not self.in_lens(function_id):
            return _cockpit_refusal(
                "role_lens",
                f"acknowledging resolves to {function_id}, which is not in the "
                f"{self.role} workspace \u2014 a role lens narrows, it never widens",
            )
        status = self._dispatch(function_id, given)
        if status.startswith(("ao-control: OK", "cockpit: OK")):
            receipt_id = self._receipt_id(status)
            self.acked[alert.id] = receipt_id
        return status

    @staticmethod
    def _receipt_id(status: str) -> str:
        for line in status.splitlines():
            if line.strip().startswith("command"):
                return line.split()[-1]
        return "acked"

    def _coerce_pairs(self, function_id: str, tokens: Sequence[str]) -> tuple[dict[str, Any], list[str]]:
        """Coerce ``name=value`` tokens into typed parameters, by name."""
        function = self.registry.functions[function_id]
        given: dict[str, Any] = {}
        errors: list[str] = []
        for token in tokens:
            if "=" not in token:
                errors.append(
                    f"PARAMETER-SYNTAX: {token!r} is not name=value "
                    f"(declared parameters: {list(function.parameter_names) or 'none'})"
                )
                continue
            name, _, raw = token.partition("=")
            parameter = function.parameter(name)
            if parameter is None:
                errors.append(
                    f"UNKNOWN-PARAMETER: {function_id} does not declare a parameter "
                    f"{name!r} (declared: {list(function.parameter_names) or 'none'})"
                )
                continue
            try:
                given[name] = _coerce(parameter, raw)
            except ValueError as exc:
                errors.append(f"PARAMETER-TYPE: {function_id}.{name}: {exc}")
        return given, errors

    # -- one-shot mode ------------------------------------------------------
    def one_shot(
        self,
        function_id: str,
        given: Mapping[str, Any],
        *,
        dry_run: bool = False,
        confirm: str = "",
        command_id: str = "",
    ) -> tuple[int, str]:
        """One declared function, once — a receipt or one named refusal.

        Exit codes are this repo's tri-state: 0 OK · 1 REFUSED · 2
        CANNOT-ASSESS (the RC-5 client's own contract, reused verbatim).
        """
        from aoctl.refusals import EXIT_OK, EXIT_REFUSED, Refusal, local_refusal

        if function_id not in self.registry.functions:
            return EXIT_REFUSED, _cockpit_refusal(
                "undeclared_function",
                f"{function_id!r} is not a declared cockpit function (RC-10)",
            )
        if not self.in_lens(function_id):
            return EXIT_REFUSED, _cockpit_refusal(
                "role_lens",
                f"{function_id} is not in the {self.role} workspace \u2014 "
                "a role lens narrows, it never widens",
            )
        call, findings = registry_io.resolve(function_id, given)
        if findings:
            return EXIT_REFUSED, "cockpit: REFUSED\n" + "\n".join(
                f"  FAIL  {finding.code}: {finding.detail}" for finding in findings
            )
        function = self.registry.functions[function_id]
        request = self.plane.request_for(
            call, command_id or plane_module.mint_command_id()
        )
        if dry_run:
            note = ""
            if function.effect_class == "irreversible" and confirm != function_id:
                note = (
                    f"{function_id} is irreversible: a real call will require "
                    f"--confirm {function_id} before anything is sent"
                )
            document = self.plane.describe(request, session_present=bool(self.session))
            lines = [
                "cockpit: DRY-RUN \u2014 nothing was sent",
                f"  {document.get('method', 'POST')} {document.get('url')}",
                f"  body        {json.dumps(document.get('body') or {}, sort_keys=True)}",
            ]
            for name in sorted(document.get("headers") or {}):
                lines.append(f"  {name:<11} {document['headers'][name]}")
            if note:
                lines.append(f"  note        {note}")
            return EXIT_OK, "\n".join(lines)
        if function.effect_class == "irreversible" and confirm != function_id:
            return EXIT_REFUSED, local_refusal(
                "confirmation_required",
                f"{function_id} declares effect_class {function.effect_class!r}; "
                f"pass --confirm {function_id}",
            ).render()
        if not self.session:
            return EXIT_REFUSED, local_refusal(
                "no_session",
                "set --session or $AO_CONTROL_SESSION; the control family's only "
                "caller identity is the console session (ADR-0025 D2)",
            ).render()
        try:
            record = self.plane.send(request, session=self.session)
        except Refusal as refusal:
            return refusal.exit_code, refusal.render()
        from aoctl.receipt import render as render_receipt

        return EXIT_OK, render_receipt(record, verb=function_id)

    # -- the interactive loop -----------------------------------------------
    def run(
        self,
        out: TextIO,
        *,
        keys: Optional[Iterable[str]] = None,
        refresh: float = 1.0,
        color: Optional[bool] = None,
    ) -> int:
        """Redraw until quit — the alternate screen, entered once (console.py's technique)."""
        import time

        frame.enable_color(out.isatty() if color is None else bool(color))
        out.write(frame.ENTER_SCREEN)
        out.flush()
        try:
            while not self.quit:
                out.write(frame.HOME + self.compose() + "\n" + frame.CLEAR_BELOW)
                out.flush()
                key = self._next_key(keys, out)
                self.status = self.handle_line(key)
                if keys is None and refresh > 0 and not self.quit:
                    time.sleep(min(max(refresh, 0.0), 60.0))
        finally:
            out.write(frame.LEAVE_SCREEN)
            out.flush()
        return 0

    @staticmethod
    def _next_key(keys: Optional[Iterable[str]], out: TextIO) -> str:
        if keys is not None:
            try:
                return next(iter(keys))
            except StopIteration:
                return "q"
        try:
            return input("cockpit \u203a ")
        except EOFError:
            return "q"


def _coerce(parameter: Any, raw: str) -> Any:
    """One raw ``name=value`` token, coerced to the declared parameter type.

    Mirrors RC-10's own CLI coercion (``control-plane/functions/cli.py``):
    integer / number / boolean / enum / string, refused with a named reason.
    """
    if parameter.type == "integer":
        return int(raw)
    if parameter.type == "number":
        return float(raw)
    if parameter.type == "boolean":
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"{raw!r} is not a boolean")
    return raw
