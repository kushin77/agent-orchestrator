"""Static-asset tests (issue #39 AC #1 + #2).

Verifies the console shell's design-token system is real and self-consistent:
* tokens.css <-> tokens.json are exact twins (names AND values, light + dark);
* every ``var(--os-*)`` referenced by CSS/HTML/JS resolves to a declared token;
* dark mode has a real ``[data-theme='dark']`` hook;
* every view is a self-contained frame linking tokens.css + console.css itself
  (per-frame CSS, no cross-frame cascade reliance);
* the console is fully offline (no external http(s) asset references).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "portal" / "static"
TOKENS_CSS = STATIC / "design-tokens" / "tokens.css"
TOKENS_JSON = STATIC / "design-tokens" / "tokens.json"
CONSOLE_CSS = STATIC / "css" / "console.css"
VIEWS = STATIC / "views"

VAR_RE = re.compile(r"var\((--[\w-]+)")
CSS_DECL_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")

#: Every view the acceptance criteria name (plus console chrome).
REQUIRED_VIEWS = [
    "overview",      # Tenant overview
    "agents",        # Agents (org tree, profiles)
    "personas",      # Personas
    "prompts",       # Prompts (versions + FP/FN)
    "policies",      # Policies/Controls (toggle, default OFF)
    "budgets",       # Budgets
    "usage",         # Usage
    "audit",         # Audit (verify chain)
    "approvals",     # Approvals feed (real-time)
]


def _strip_comments(css_text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css_text, flags=re.DOTALL)


def _split_dark(css_text: str) -> tuple[str, str]:
    css_text = _strip_comments(css_text)
    index = css_text.find(".dark,")
    if index == -1:
        return css_text, ""
    return css_text[:index], css_text[index:]


def _declared(css_text: str) -> dict[str, str]:
    return {
        name: value.strip()
        for name, value in CSS_DECL_RE.findall(css_text)
    }


def _load_json() -> dict:
    return json.loads(TOKENS_JSON.read_text(encoding="utf-8"))


# -- token twin parity ------------------------------------------------------

def test_tokens_json_is_valid_json():
    data = _load_json()
    assert data["namespace"] == "--os-"
    assert data["meta"]["issue"] == "kushin77/agent-orchestrator#39"


def test_tokens_css_json_light_twins_match():
    css_text = TOKENS_CSS.read_text(encoding="utf-8")
    light_text, dark_text = _split_dark(css_text)
    light_declared = _declared(light_text)
    data = _load_json()
    json_light = data["css"]
    # Same set of names (exact twins, no drift in either direction).
    assert set(json_light) == set(light_declared), (
        "css/json light sets differ: only-css=" +
        ",".join(sorted(set(light_declared) - set(json_light))) +
        " only-json=" + ",".join(sorted(set(json_light) - set(light_declared)))
    )
    for name, value in json_light.items():
        assert light_declared[name] == value, f"light value mismatch for {name}"


def test_tokens_css_json_dark_twins_match():
    css_text = TOKENS_CSS.read_text(encoding="utf-8")
    _, dark_text = _split_dark(css_text)
    dark_declared = _declared(dark_text)
    data = _load_json()
    json_dark = data["modes"]["dark"]["css"]
    assert set(json_dark) == set(dark_declared)
    for name, value in json_dark.items():
        assert dark_declared[name] == value, f"dark value mismatch for {name}"
    # Dark overrides reference only tokens that exist in the light set.
    light = set(_load_json()["css"])
    assert set(json_dark) <= light


def test_dark_mode_hook_is_real():
    css_text = TOKENS_CSS.read_text(encoding="utf-8")
    for hook in ("[data-theme='dark']", ".dark"):
        assert hook in css_text, f"missing dark hook {hook}"
    js = (STATIC / "js" / "api.js").read_text(encoding="utf-8")
    assert "data-theme" in js


# -- every referenced token is declared -------------------------------------

def _referenced_tokens(text: str) -> set[str]:
    return set(VAR_RE.findall(text))


@pytest.mark.parametrize(
    "path",
    [CONSOLE_CSS]
    + sorted(STATIC.joinpath("js").glob("*.js"))
    + sorted(VIEWS.glob("*.html")),
    ids=lambda p: str(p.relative_to(STATIC)),
)
def test_referenced_tokens_are_declared(path):
    data = _load_json()
    known = set(data["css"]) | set(data["modes"]["dark"]["css"])
    text = path.read_text(encoding="utf-8")
    unknown = _referenced_tokens(text) - known
    assert not unknown, f"{path.relative_to(STATIC)} references undeclared tokens: {unknown}"


# -- per-frame no-cascade + offline ------------------------------------------

@pytest.mark.parametrize("view", REQUIRED_VIEWS)
def test_view_is_self_contained_frame(view):
    html = (VIEWS / f"{view}.html").read_text(encoding="utf-8")
    # The frame links the token twin + console css itself (no inherited
    # cascade from a parent document).
    assert 'href="/design-tokens/tokens.css"' in html, view
    assert 'href="/css/console.css"' in html, view
    assert 'src="/js/api.js"' in html, view
    assert "<html" in html and "<body" in html


@pytest.mark.parametrize(
    "path",
    sorted(STATIC.rglob("*.html"))
    + sorted(STATIC.rglob("*.css"))
    + sorted(STATIC.rglob("*.js")),
    ids=lambda p: str(p.relative_to(STATIC)),
)
def test_no_external_asset_references(path):
    text = path.read_text(encoding="utf-8")
    for marker in ("http://", "https://", "//fonts.googleapis", "cdn."):
        assert marker not in text, f"{path.relative_to(STATIC)} references {marker!r}"


def test_console_shell_and_login_present():
    assert (VIEWS / "shell.html").exists()
    assert (VIEWS / "tenants.html").exists()
    assert (VIEWS / "login.html").exists()


def test_provenance_document_exists():
    provenance = STATIC / "design-tokens" / "PROVENANCE.md"
    assert provenance.exists()
    text = provenance.read_text(encoding="utf-8")
    assert "tokens.css" in text and "tokens.json" in text
    assert "elevatediq" in text
