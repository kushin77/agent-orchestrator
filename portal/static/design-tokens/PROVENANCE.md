# Portal design tokens — provenance (issue #39)

The console is styled from the harvested **OS-portal design-token system**
(CSS + JSON twins), never from ad-hoc colors. `tokens.css` is the authoring
source of truth for CSS consumers; `tokens.json` is its machine-readable
twin (parity-checked by `portal/tests/test_assets.py` so the twins can never
drift).

## Rule

No token value below is invented. Every value is grounded in a real
token/theme file from one of the harvested upstream repos (revs are
`git rev-parse --short HEAD` at harvest time, 2026-09-04/05). Where an
upstream had no grounded source it is documented, never fabricated.

## Source table

| Token group | Source repo | Source file(s) | Rev |
|---|---|---|---|
| Brand + semantic (light) | elevatediq-website | `website/static-site/css/styles.css` (`--primary/--accent/--success/--warning/--error`), `design-system/DESIGN_SYSTEM.md` (`info`) | `ce01f9f` |
| `primary-fg` (text on primary) | capital-underwriting | `apps/frontend/vibe/src/index.css` (`--primary-foreground`) | `543e63b` |
| Text/surfaces/borders (light) | elevatediq-website | `website/static-site/css/styles.css` | `ce01f9f` |
| Neutral ramp `gray-50…900` | elevatediq-website | `website/static-site/css/styles.css` | `ce01f9f` |
| Space scale (4px grid) | elevatediq-website (+ cross-check Gov-AI-Scout) | `website/static-site/css/styles.css` (`--space-1…16`) | `ce01f9f`, `c8fc5c3` |
| Font families | Gov-AI-Scout + elevatediq-website | marketing `tailwind.config.js` (Inter stack); `DESIGN_SYSTEM.md` (Poppins/Fira Code) | `c8fc5c3`, `ce01f9f` |
| Type scale / weights / line heights | elevatediq-website | `design-system/DESIGN_SYSTEM.md` | `ce01f9f` |
| Radius | elevatediq-website (+ cross-check capital-underwriting) | `DESIGN_SYSTEM.md`; CU `--radius: .75rem` | `ce01f9f`, `543e63b` |
| Shadow | elevatediq-website (+ cross-check Gov-AI-Scout) | `styles.css` (`--shadow-*`); GAS `boxShadow.xl` for 2xl | `ce01f9f`, `c8fc5c3` |
| Motion | elevatediq-website | `styles.css` (`--transition-*`) | `ce01f9f` |
| Governance accents (cyan/drift/compliant/schematic) | OS chrome `shell/src/styles.css` + Gov-AI-Scout `portal/app/globals.css` (dark cyan) | `shell/src/styles.css`, `portal/app/globals.css` | shell `8a197da`, `c8fc5c3` |
| Blueprint canvas (dark surfaces) | OS chrome `shell/src/styles.css` | `--color-bg-page #0f1220`, `--color-surface #1a1f36`, `--color-surface-subtle #232a47`, `--color-border #2c3454` | `8a197da` |
| Dark text tones | OS chrome `shell/src/styles.css` (fg ramp `#e7f3f7`/`#9db8c9`/`#5d7c8e`) | `shell/src/styles.css` | `8a197da` |

## Documented local choices (flagged, not hidden)

- **Dark destructive `--os-color-error` stays `#ef4444`** (the elevatediq
  light signal value) instead of a dark-red surface tone: it must read as an
  error on the dark canvas. CMR's portal made the same documented choice
  (`--bp-red`).
- **No dark palette exists in elevatediq** — dark surfaces use the OS chrome
  blueprint canvas; dark semantic overrides are consolidated in the
  `tokens.json` `modes.dark.css` block and the `tokens.css` dark selector.

## How the console consumes the twins

- Every frame (shell + each view) links `tokens.css` **independently** — there
  is no cross-frame cascade to rely on (each view is an isolated document /
  same-origin frame that applies its own theme from `localStorage`).
- `console.css` + the view styles reference `var(--os-*)` tokens only.
- Dark mode is the `[data-theme='dark']` hook set by `console.js` on the root
  of every frame.

## Validation (must pass)

```sh
python3 -c "import json; json.load(open('portal/static/design-tokens/tokens.json'))"
# twin parity is asserted in portal/tests/test_assets.py
```
