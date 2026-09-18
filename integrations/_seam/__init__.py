"""The seam `integrations/paperclip/` and `integrations/hermes/` share (#1208).

The two peer adapters were written one after the other, and the second is a
systematic copy of the first: the same YAML-subset loader, the same
JSON-Schema-subset validator, the same ``Response``/``error_for_status`` pair,
the same transport ``Protocol`` and the same fixture transport — same private
helper names, same bodies, near-identical docstrings. By the time this seam was
cut the copies had already drifted: paperclip's validator enforced
``format: date-time``, ``minimum`` and ``maximum``, and hermes's did not. A fix
that landed in one adapter was a fix the other never received.

This package is the single home for those parts:

* :mod:`.yaml_subset` — the stdlib-only YAML subset loader (``load_yaml``);
* :mod:`.schema` — the stdlib-only JSON-Schema subset validator (``validate``);
* :mod:`.wire` — ``Response``, ``decode`` and the ``error_for_status`` factory;
* :mod:`.transport` — the ``Transport`` Protocol and the offline
  ``FixtureTransport``.

What the seam does **not** take is what genuinely differs between the two
adapters, and taking it would have been a behaviour change rather than a
de-duplication:

* their own typed errors (``PaperclipError`` … / ``HermesError`` …) — only the
  *shape* of the message and the status→class lookup are shared, through
  :class:`~integrations._seam.wire.WireBoundary`;
* their own live ``HttpTransport``: paperclip's carries ``Authorization: Bearer``
  and ``X-Paperclip-Run-Id`` and owns the ``/api`` prefix, hermes's is keyless
  and reads root-level ``/health``. Both send no header they did not send before;
* their own source shapes, mappers and contracts.

Nothing here changes either adapter's wire behaviour: exactly what was already
identical between the two moved, and what was *not* identical was unified **up**
— the shared validator is paperclip's superset, and the shared loader is the
union (hermes's flow-mapping support, which paperclip's copy lacked).

Every name below is re-exported, so the seam reads as one module: import from
``integrations._seam``, never from a submodule.
"""

from __future__ import annotations

from .schema import validate
from .transport import FixtureTransport, Transport
from .wire import Response, WireBoundary, decode, error_for_status
from .yaml_subset import load_yaml, load_yaml_file

__all__ = [
    "FixtureTransport",
    "Response",
    "Transport",
    "WireBoundary",
    "decode",
    "error_for_status",
    "load_yaml",
    "load_yaml_file",
    "validate",
]
