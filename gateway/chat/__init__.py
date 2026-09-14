"""gateway.chat — the conversational serving surface (issue #503, ADR-0023).

The OpenAI- and Ollama-compatible endpoints of the control plane: this package
is the **mount** of ``gateway/proxy``'s transport-free dispatch core, and it
composes — never re-implements — the four sibling chat authorities:

======================  ==========================================================
authority               what this package consumes it for
======================  ==========================================================
``gateway/proxy``       the one model path: routing, tier choice, caps,
                        budgets, fallback and the ``GatewayCallRecord`` emitted
                        to the audit + metering sinks on every dispatch
``identity/chat``       the front door: the scoped ``(tenant, agent,
                        conversation)`` credential — identity comes from the
                        verified credential, never from a request body
``telemetry/chat``      the per-turn budget guard (kill switch **before** the
                        model call), the attribution rows and the tier stamp
``guardrails/chat``     retrieval-injection defense, DLP egress (abort on block)
                        and inbound re-validation of the answer
``gateway/mcp``         the grounding hand-off: the assembled prefix and the tool
                        declarations pass through unchanged
``registry/chat``       the published prompt modules a turn runs under
``engine/memory``       the conversation container (``session:<tenant>:<agent>:<session>``,
                        reached through ``identity/chat``'s isolation)
======================  ==========================================================

Import as ``chat`` with ``gateway/`` on ``sys.path`` (the sibling gateway
packages' convention) or as ``gateway.chat`` with the repository root on
``sys.path``; both work, and the relative imports inside the package mean the
two styles cannot diverge.  ``gateway/`` itself stays ``__init__.py``-free.

Public surface
--------------

- :class:`ChatSurface` — the endpoints: ``POST /v1/chat/completions``
  (JSON + SSE), ``POST /api/chat`` (JSON + NDJSON) and ``GET /v1/models``.
- :class:`~gateway.chat.flags.SurfaceFlags` helpers — ``surface_enabled`` /
  ``require_surface``, checked **before** AuthN.
- :class:`~gateway.chat.models.ModelCatalogue` — the derived model list.
- :class:`~gateway.chat.resolver.ChatTaskResolver` — the proxy ``TaskResolver``
  seam over ``registry/chat``.
- :mod:`gateway.chat.wiring` — the composition root that wires the real
  siblings into a surface.
"""

from __future__ import annotations

import sys
from pathlib import Path

# gateway/chat/__init__.py -> gateway/chat -> gateway -> repository root
_HERE = Path(__file__).resolve()
_GATEWAY_ROOT = _HERE.parents[1]
_REPO_ROOT = _HERE.parents[2]
# The repository root first (so ``identity.chat``, ``telemetry.chat`` and
# ``gateway.mcp`` resolve), then the gateway root (so ``proxy``, ``providers``
# and ``limits`` resolve with the plain module names their own packages use).
# Both insertions are guarded, and the package's own imports are relative, so
# importing this module as ``gateway.chat`` and as ``chat`` reaches the same
# code.
for _path in (str(_GATEWAY_ROOT), str(_REPO_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from .contract import (  # noqa: E402
    ID_PREFIX,
    SSE_DONE,
    ChatTurnRequest,
    parse_ollama_request,
    parse_openai_request,
)
from .conversation import ConversationStore, replay_messages  # noqa: E402
from .errors import (  # noqa: E402
    BudgetRefused,
    ChatSurfaceError,
    CredentialRefused,
    CredentialRequired,
    CrossTenantRefused,
    GroundingUnreadable,
    MalformedRequest,
    ModelNotSelectable,
    SurfaceDisabled,
    TurnRefused,
    UngroundedResponse,
    UnknownModel,
)
from .flags import (  # noqa: E402
    DEFAULT_REGISTRY_PATH,
    SURFACE_KEY,
    read_surface_default,
    require_surface,
    surface_enabled,
)
from .models import CatalogUnavailable, ModelCatalogue  # noqa: E402
from .resolver import (  # noqa: E402
    ChatRouteError,
    ChatTaskResolver,
    ResolvedChatTask,
    chat_routing_config,
    task_type_for,
)
from .surface import (  # noqa: E402
    GROUNDING_NO_DATA,
    GROUNDING_OK,
    STREAM_CHUNK_CHARS,
    ChatSurface,
    TurnPlan,
    TurnRecord,
    history_for,
)

__all__ = [
    "BudgetRefused",
    "CatalogUnavailable",
    "ChatRouteError",
    "ChatSurface",
    "ChatSurfaceError",
    "ChatTaskResolver",
    "ChatTurnRequest",
    "ConversationStore",
    "CredentialRefused",
    "CredentialRequired",
    "CrossTenantRefused",
    "DEFAULT_REGISTRY_PATH",
    "GROUNDING_NO_DATA",
    "GROUNDING_OK",
    "GroundingUnreadable",
    "ID_PREFIX",
    "MalformedRequest",
    "ModelCatalogue",
    "ModelNotSelectable",
    "ResolvedChatTask",
    "SSE_DONE",
    "STREAM_CHUNK_CHARS",
    "SURFACE_KEY",
    "SurfaceDisabled",
    "TurnPlan",
    "TurnRecord",
    "TurnRefused",
    "UngroundedResponse",
    "UnknownModel",
    "chat_routing_config",
    "history_for",
    "parse_ollama_request",
    "parse_openai_request",
    "read_surface_default",
    "replay_messages",
    "require_surface",
    "surface_enabled",
    "task_type_for",
]
