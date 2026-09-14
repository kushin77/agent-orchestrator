# paperclip skills adapter — the `SKILL.md` registry and the MCP tool projection

The fleet-side producer for upstream's **extensibility** family: skills
(`SKILL.md`), plugins/extensions, and MCP tool access (EPIC #410 child #419,
ADR-0013). It maps that family onto what the fleet already runs — the tool
authority under `../../../gateway/mcp/`, the agent profile under
`../../../registry/profiles/`, and a closed registry of declarations here.

It adds **no ledger and no second authority**: it derives, refuses, and reports.

## The four rules, and where each is enforced

| Rule | Enforced by |
|---|---|
| The registry is **closed** — an undeclared `SKILL.md` is refused, never silently loaded | [`registry.py`](registry.py), [`registry.json`](registry.json) |
| MCP tools are **projected, not duplicated** — a callable tool absent from the projection is a FAIL | [`projection.py`](projection.py), [`mcp_tools.json`](mcp_tools.json) |
| A skill or plugin requiring a capability or tool the profile does not grant is **refused at load** | [`loader.py`](loader.py) |
| No upstream code is **vendored** — a declaration plus a reference, never a copy | [`frontmatter.py`](frontmatter.py) |

## Where the projected tool list comes from

The projection is a **view** of the tool authority, never a hand-maintained
allowlist. [`projection.py`](projection.py) imports
`gateway.mcp.tools.build_registry()` — declared in `../../../gateway/mcp/tools.py`
— and writes the derived surface to [`mcp_tools.json`](mcp_tools.json), together
with the source function it was derived from and the closed vocabulary
`MCP_ALLOWLIST_KEYS` in `../../../gateway/mcp/model.py`.

The gate therefore fails, by name, on either direction of drift:

- a tool the authority declares **callable** but the projection omits
  (`gateway/mcp/` changed, the view did not);
- a tool the projection names but the authority does not declare callable
  (the view was edited by hand);
- a disagreement between the authority's registry and its closed vocabulary.

## The declarations

A declaration is a `SKILL.md` carrying a YAML front-matter block with its
**origin and provenance** (GR-10 — `repo`, `path`, `license`, `verdict`) and the
requirements it makes of the loading agent:

- `library/` — instructions an agent loads (skills);
- `plugins/` — declarations that additionally wire an internal tool the profile
  must already grant.

The registry only says *which* declarations are loadable. The declaration
directory may contain `SKILL.md` and nothing else: a copied implementation is a
vendoring violation, refused by name.

## Usage

```bash
python3 -m paperclip.adapters.skills.cli check
python3 -m paperclip.adapters.skills.cli project --check
python3 -m paperclip.adapters.skills.cli list
python3 -m paperclip.adapters.skills.cli show --skill ticket-contract-read
python3 -m paperclip.adapters.skills.cli load --skill ticket-contract-read --profile paperclip
```

Exit-code contract (the repo's tri-state): `0` OK / `1` NOT-OK / `2`
CANNOT-ASSESS. A projection that cannot be derived from the authority is
CANNOT-ASSESS, never a pass.

The gate of record for this adapter is
[`../../../scripts/check-paperclip-skills.sh`](../../../scripts/check-paperclip-skills.sh);
it provokes each refusal and names the offender. See
[`PROVENANCE.md`](PROVENANCE.md) for the harvest record and
`../../../docs/PAPERCLIP-ING-INTEGRATION.md` for the seam this adapter sits on.
