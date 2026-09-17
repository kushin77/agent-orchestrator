"""The declared environment-variable surface, tested (issue #944).

`infra/env/registry.yaml` is the ONE declared place for the environment
variables this repository's code reads, and `infra/env/surface.py` measures that
declaration against the tree. This module is that pair's own test, and it covers
what `scripts/check-env-surface.sh` deliberately does not: the gate *runs* the
checker and provokes its refusals, while these assertions pin the parts — the
schema, the reader claims, the parser, and the exit-code contract.

What is asserted here is the point of the issue, not a copy of its prose:

* the declaration parses against its own schema and is **closed over the
  committed tree** — `check()` returns no findings, asserted rather than assumed;
* the four variables the issue names are declared or exempted, so the issue's own
  `Verify:` has a target to grep;
* every declared `readers` entry is a real file that really names the variable —
  "who reads it" is measured, never merely claimed;
* the scanner resolves the console's module-level-constant idiom, which is the
  property that makes it *not a grep*, and the negative control proves the
  textual scan a naive gate would use finds nothing at all — the false green the
  issue is about;
* a variable the code reads but the declaration does not account for is **named**
  (`undeclared-env-read`) — the direction the issue exists for — and the reverse
  is named too (`declared-env-unread`);
* a `secret: true` variable is refused as a literal and accepted as a placeholder
  or an indirection (GR-6);
* the CLI honours its 0 / 1 / 2 contract.

Run: `python3 -m pytest infra/env/tests -q`
"""

from __future__ import annotations

import dataclasses
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SURFACE_PY = ROOT / "infra" / "env" / "surface.py"
REGISTRY_YAML = ROOT / "infra" / "env" / "registry.yaml"

#: The four names the issue's `Verify:` greps for. They were read by the code and
#: declared nowhere, which is the defect #944 records.
ACCEPTANCE = (
    "AO_SURFACE_REGISTRY",
    "AO_EDGE_HOST",
    "PORTAL_AUTH_GATE_JWKS",
    "ROOT_ADMIN_EMAILS",
)

#: The textual scan a gate would use if it were a grep. Kept here as the negative
#: control: it is the shape that finds NOTHING in `portal/`.
NAIVE_SCAN = re.compile(r'os\.environ(?:\.get)?[\[(]\s*"([A-Z][A-Z0-9_]{2,})"')


def _load_surface():
    """Load `infra/env/surface.py` by path — it is a script, not an installed package.

    The module is registered in `sys.modules` before it is executed: the checker
    declares frozen dataclasses, and `dataclasses` resolves annotations through
    `sys.modules[cls.__module__]`, so an unregistered module raises
    `AttributeError: 'NoneType' object has no attribute '__dict__'` at import.
    """
    name = "ao_env_surface_under_test"
    spec = importlib.util.spec_from_file_location(name, SURFACE_PY)
    assert spec is not None and spec.loader is not None, f"cannot load {SURFACE_PY}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


surface = _load_surface()


@pytest.fixture(scope="module")
def registry():
    return surface.load_registry(REGISTRY_YAML)


# --- the declaration ---------------------------------------------------------


def test_the_declaration_parses_and_declares_its_schema(registry):
    assert registry.schema == surface.REGISTRY_SCHEMA
    assert registry.roots, "a declaration that scans no root is closed over nothing"
    assert registry.variables, "the declaration names no variable at all"
    assert surface.schema_findings(registry) == []


def test_the_declaration_is_closed_over_the_committed_tree(registry):
    """The headline claim, measured: the declaration and the tree agree."""
    findings = surface.check(ROOT, registry)
    assert findings == [], "the declared surface drifts from the tree:\n" + "\n".join(findings)


@pytest.mark.parametrize("name", ACCEPTANCE)
def test_the_variables_the_issue_names_are_declared(registry, name):
    assert name in registry.declared_names() | registry.exempt_names(), (
        f"{name} is read by this repository's code and declared nowhere (#944)"
    )


def test_the_scope_is_declared_with_a_reason_rather_than_left_implicit(registry):
    """A root that is not scanned is an omission unless the reason is written down."""
    assert "infra" in registry.roots, "infra/ is where the deploy variables live"
    assert registry.exclude, "the exclusions must be declared, not implied"
    for path, reason in registry.exclude.items():
        assert len(reason) >= surface.MIN_REASON, f"exclusion of {path} carries no reason"


def test_every_variable_records_a_reader_that_exists_and_names_it(registry):
    """`readers` is a measurement, so it must survive being read back."""
    assert surface.reader_findings(ROOT, registry) == []
    for variable in registry.variables:
        assert variable.readers, f"{variable.name} records no reader"
        for rel in variable.readers:
            path = ROOT / rel
            assert path.is_file(), f"{variable.name} records a reader that is not in the tree: {rel}"
            text = path.read_text(encoding="utf-8", errors="replace")
            assert variable.name in text, f"{rel} is recorded as a reader of {variable.name} but never names it"


def test_each_exemption_carries_a_reason(registry):
    for exemption in registry.exempt:
        assert len(exemption.reason) >= surface.MIN_REASON, (
            f"{exemption.name} is exempted without a reason, which is an omission"
        )


# --- the scanner is deliberately not a grep ----------------------------------

CONSOLE_IDIOM = '''\
import os

JWKS_ENV = "PORTAL_AUTH_GATE_JWKS"
ROOT_ADMIN_ENV = "ROOT_ADMIN_EMAILS"


def configured(jwks_file: str) -> str:
    inline = (os.environ.get(JWKS_ENV) or "").strip()
    return inline or (os.environ.get(ROOT_ADMIN_ENV, "") or jwks_file)
'''


def test_a_read_through_a_module_constant_is_resolved(tmp_path, registry):
    """The console's idiom — and the negative control that makes it meaningful."""
    planted = tmp_path / "console_idiom.py"
    planted.write_text(CONSOLE_IDIOM, encoding="utf-8")

    measured = surface.measure_one(planted, registry)
    assert {"PORTAL_AUTH_GATE_JWKS", "ROOT_ADMIN_EMAILS"} <= set(measured.reads), measured.reads
    assert measured.indirect == {}

    # The control: a textual scan of the SAME source yields no name at all. If the
    # scanner were a grep, the console would read four variables while this gate
    # reported the surface closed — the false green #944 is about.
    assert NAIVE_SCAN.findall(CONSOLE_IDIOM) == []


def test_a_read_the_scanner_cannot_resolve_is_counted_not_silently_dropped(tmp_path, registry):
    planted = tmp_path / "dynamic_read.py"
    planted.write_text(
        "import os\n\n\ndef read(name: str) -> str:\n    return os.environ.get(name, '')\n",
        encoding="utf-8",
    )
    measured = surface.measure_one(planted, registry)
    assert measured.reads == {}, "a dynamic read must not be reported as a named variable"
    assert measured.indirect == {str(planted): 1}, measured.indirect


# --- the direction the issue exists for --------------------------------------


def test_a_variable_the_code_reads_but_the_declaration_omits_is_named(registry):
    """The defect: `AO_SURFACE_REGISTRY` was read by 6 files and declared nowhere."""
    assert "AO_SURFACE_REGISTRY" in registry.declared_names(), "precondition"

    blind = dataclasses.replace(
        registry,
        variables=tuple(v for v in registry.variables if v.name != "AO_SURFACE_REGISTRY"),
    )
    findings = surface.undeclared_findings(blind, surface.measure(ROOT, blind))
    assert any(
        surface.CODE_UNDECLARED in finding and "AO_SURFACE_REGISTRY" in finding
        for finding in findings
    ), f"an undeclared read went unreported:\n" + "\n".join(findings)


def test_a_declared_variable_nothing_reads_is_named(registry):
    ghost = surface.Variable(
        name="AO_NEVER_READ_ANYWHERE",
        purpose="a variable that exists only inside this test",
        secret=False,
        required_by=("ops",),
        readers=("infra/env/README.md",),
    )
    mutated = dataclasses.replace(registry, variables=registry.variables + (ghost,))
    findings = surface.unread_findings(ROOT, mutated, surface.measure(ROOT, mutated))
    assert any(
        surface.CODE_UNREAD in finding and "AO_NEVER_READ_ANYWHERE" in finding
        for finding in findings
    ), f"a stale declaration entry went unreported:\n" + "\n".join(findings)


# --- a declared secret is never carried as a value (GR-6) --------------------


@pytest.mark.parametrize("secret", ["KEYDB_PASSWORD", "AO_DLP_HMAC_KEY"])
def test_a_declared_secret_is_refused_as_a_literal(tmp_path, registry, secret):
    assert secret in registry.secret_names(), "precondition"
    leak = tmp_path / f"{secret}.env"
    leak.write_text(f"{secret}=Swordfish-42\n", encoding="utf-8")
    findings = surface.secret_findings_in(leak, registry)
    assert any(surface.CODE_SECRET_LITERAL in f and secret in f for f in findings), findings


@pytest.mark.parametrize(
    "value", ["change-me-before-deploy", "placeholder", "${KEYDB_PASSWORD:-}", "$(vault read kv/keydb)"]
)
def test_a_secret_reference_or_placeholder_in_the_same_shape_is_accepted(tmp_path, registry, value):
    twin = tmp_path / "twin.env"
    twin.write_text(f"KEYDB_PASSWORD={value}\n", encoding="utf-8")
    assert surface.secret_findings_in(twin, registry) == []


# --- the CLI contract --------------------------------------------------------


def test_the_cli_honours_its_exit_code_contract(tmp_path):
    # 0 — the declaration as committed holds.
    assert surface.main(["check", "--root", str(ROOT)]) == 0

    # 1 — a named variable the declaration does not account for.
    planted = tmp_path / "undeclared.py"
    planted.write_text(
        'import os\n\nUNDECLARED = "AO_TOTALLY_UNDECLARED_BY_ANY_DECLARATION"\n\n\n'
        "def read() -> str:\n    return os.environ.get(UNDECLARED, '')\n",
        encoding="utf-8",
    )
    assert surface.main(["scan-file", "--root", str(ROOT), "--file", str(planted)]) == 1

    # 2 — CANNOT-ASSESS: there is no declaration to measure. Never a pass.
    assert surface.main(["check", "--root", str(tmp_path)]) == 2
