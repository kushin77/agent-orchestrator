"""paperclip.ing integration adapter (issue #428, ADR-0013).

The fleet adopts the upstream Paperclip CLI as an external operator surface and
integrates over its HTTP API across a process boundary (ADR-0013). This package
is the adapter that maps the fleet's own stores (registry seeds + persona cards,
the claim ledger + board snapshot, the budget rail) onto the upstream surface,
through the three frozen seam contracts in ``docs/contracts/paperclip/``.

The package is stdlib-only: the HTTP transport uses ``urllib.request`` and the
mapping reads its YAML sources with a small in-repo subset loader, so the gate
and the tests never need a third-party dependency or the network.
"""

__all__ = ["client", "mapping", "model"]
