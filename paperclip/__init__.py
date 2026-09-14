"""The Paperclip-ing integration: adapters that map fleet state to the upstream seams.

The seam contracts are frozen under ``docs/contracts/paperclip/`` (ADR-0013);
the adapters that fill them live here. Each adapter only *derives* the upstream
shape — the fleet stays the writer of its own runtime (ADR-0012).
"""
