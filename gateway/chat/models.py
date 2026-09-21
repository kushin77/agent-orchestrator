"""gateway.chat.models — the advertised model list, DERIVED (issue #503 scope 1).

``GET /v1/models`` is not a hand-written list, and it must not become one: the
list is a projection of two authorities that already exist and are already
gate-enforced —

* ``gateway/catalog/modules/*/module.json`` — the module catalog: which model
  providers this control plane ships, their package, version, classes and
  features.  One entry per catalog module, discovered by glob (a module added
  to the catalog appears here with no edit to this file).
* ``gateway/proxy/config/routing.yaml`` — the routing policy: the provider
  chains per registry tier, which is the only authority on which of those
  providers is actually *routable*.

and the **selectable** ids are the tier ladder pinned from
``gateway/providers/contract.py`` (``TIERS``), because the surface selects a
tier and never a provider model (ADR-0023 §4: the chooser resolves the model).
A provider module id is advertised for discovery with ``selectable: false`` and
is refused as a request ``model`` — asking for one is refused rather than
honoured, so a client cannot address a provider directly.

``created`` is deliberately absent: OpenAI's field is a creation timestamp and
this surface has no such fact about a provider module.  Inventing one is the
fabrication AO-GR-19 forbids, so the field is omitted instead.

---knowledge---
module_id: gateway.chat.models
system: gateway
app: chat
solution_class: enterprise
patterns: [derived-never-hand-written, projection, read-only-authority]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [ModelCatalogue, CatalogModule, CatalogUnavailable, advertised_ids]
invariants: "the advertised list is a projection of the module catalog and the routing policy; it is never a hand-written list"
gotchas: "a module added to the catalog appears here with no edit to this file, discovered by glob"
related: ["#503"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from .errors import ChatSurfaceError

#: gateway/chat/models.py -> gateway/chat -> gateway -> repository root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GATEWAY_DIR = REPO_ROOT / "gateway"
CATALOG_DIR = GATEWAY_DIR / "catalog" / "modules"
ROUTING_PATH = GATEWAY_DIR / "proxy" / "config" / "routing.yaml"

#: The catalog entry point every module document declares.
MODULE_SCHEMA = "cmr.module/v1"


class CatalogUnavailable(ChatSurfaceError):
    """The catalog or routing authority could not be read (fail closed)."""

    status = 503
    code = "catalogue_unavailable"
    error_type = "server_error"


def _load_tier_ladder() -> tuple[str, ...]:
    """The selectable tier ladder, read from the provider contract.

    ``gateway/providers/contract.py`` is the authority for ``TIERS``; it is
    loaded by path under a unique module name so importing it here cannot
    collide with a sibling ``contract`` module while the proxy's own packages
    are on ``sys.path``.
    """
    path = GATEWAY_DIR / "providers" / "contract.py"
    if not path.is_file():
        raise CatalogUnavailable(f"provider contract is missing: {path}")
    name = "ao_chat_provider_contract"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, str(path))
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise CatalogUnavailable(f"cannot load provider contract from {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    tiers = getattr(module, "TIERS", ())
    if not tiers:
        raise CatalogUnavailable(f"provider contract {path} declares no TIERS")
    return tuple(str(tier) for tier in tiers)


def _load_routing_config(routing_path: Optional[Path] = None):
    """The proxy's own routing config loader (consumed, never re-implemented)."""
    if str(GATEWAY_DIR) not in sys.path:
        sys.path.insert(0, str(GATEWAY_DIR))
    from proxy.router import load_routing_config

    return load_routing_config(Path(routing_path) if routing_path else ROUTING_PATH)


@dataclass(frozen=True)
class CatalogModule:
    """One provider module of the module catalog."""

    module_id: str
    name: str
    version: str
    provider_key: str
    package: str
    classes: tuple[str, ...]
    features: tuple[str, ...]

    def to_entry(self, *, routable: bool, tiers: tuple[str, ...]) -> dict[str, Any]:
        return {
            "id": self.module_id,
            "object": "model",
            "owned_by": self.provider_key,
            "ao": {
                "kind": "provider-module",
                "selectable": False,
                "routable": routable,
                "provider": self.provider_key,
                "version": self.version,
                "package": self.package,
                "classes": list(self.classes),
                "tiers": list(tiers),
                "note": (
                    "advertised for discovery only: a request selects a tier, "
                    "never a provider model (ADR-0023)"
                ),
            },
        }


@dataclass(frozen=True)
class ModelCatalogue:
    """The derived advertisement: the tier ladder plus the shipped providers."""

    tiers: tuple[str, ...]
    modules: tuple[CatalogModule, ...]
    entries: tuple[dict[str, Any], ...]
    chains: Mapping[str, tuple[str, ...]]
    source: Mapping[str, str]

    # -- construction ------------------------------------------------------ #
    @classmethod
    def discover(
        cls,
        *,
        catalog_dir: Optional[Path] = None,
        routing_path: Optional[Path] = None,
    ) -> "ModelCatalogue":
        """Read the catalog + routing policy and project the model list."""
        tiers = _load_tier_ladder()
        routing = _load_routing_config(routing_path)
        chains = {
            str(tier): tuple(str(p) for p in chain)
            for tier, chain in routing.provider_chains.items()
        }
        routable = {provider for chain in chains.values() for provider in chain}

        directory = Path(catalog_dir) if catalog_dir is not None else CATALOG_DIR
        if not directory.is_dir():
            raise CatalogUnavailable(f"module catalog directory is missing: {directory}")
        modules = tuple(
            _read_module(document)
            for document in sorted(directory.glob("*/module.json"))
        )
        if not modules:
            raise CatalogUnavailable(
                f"module catalog {directory} holds no */module.json documents"
            )

        entries: list[dict[str, Any]] = []
        for tier in tiers:
            providers = next(
                (chain for name, chain in chains.items() if name == tier), ()
            )
            entries.append(
                {
                    "id": tier,
                    "object": "model",
                    "owned_by": "agent-orchestrator/gateway",
                    "ao": {
                        "kind": "tier",
                        "selectable": True,
                        "tier": tier,
                        "providers": list(providers),
                        "note": (
                            "a selectable tier id: the FinOps chooser resolves "
                            "the provider model behind it"
                        ),
                    },
                }
            )
        module_tiers: dict[str, tuple[str, ...]] = {}
        for module in modules:
            module_tiers[module.module_id] = tuple(
                tier
                for tier, chain in chains.items()
                if module.provider_key in chain
            )
            entries.append(
                module.to_entry(
                    routable=module.provider_key in routable,
                    tiers=module_tiers[module.module_id],
                )
            )
        return cls(
            tiers=tiers,
            modules=modules,
            entries=tuple(entries),
            chains=chains,
            source={
                "catalog": str(directory),
                "routing": str(
                    Path(routing_path) if routing_path is not None else ROUTING_PATH
                ),
                "providerContract": str(GATEWAY_DIR / "providers" / "contract.py"),
            },
        )

    # -- queries ----------------------------------------------------------- #
    @property
    def selectable_ids(self) -> tuple[str, ...]:
        return self.tiers

    @property
    def advertised_ids(self) -> tuple[str, ...]:
        return tuple(str(entry["id"]) for entry in self.entries)

    def find(self, model_id: str) -> Optional[dict[str, Any]]:
        for entry in self.entries:
            if entry["id"] == model_id:
                return entry
        return None

    def is_selectable(self, model_id: str) -> bool:
        entry = self.find(model_id)
        return bool(entry and entry["ao"]["selectable"])

    def to_openai_list(self) -> dict[str, Any]:
        """The OpenAI `GET /v1/models` body, with the derivation disclosed."""
        return {
            "object": "list",
            "data": [dict(entry) for entry in self.entries],
            "ao": {
                "derivedFrom": dict(self.source),
                "selectable": list(self.tiers),
                "selects": "tier",
            },
        }


def _read_module(path: Path) -> CatalogModule:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CatalogUnavailable(f"catalog module {path} is not readable: {exc}") from exc
    if not isinstance(document, Mapping) or document.get("schema") != MODULE_SCHEMA:
        raise CatalogUnavailable(
            f"catalog module {path} is not a {MODULE_SCHEMA} document"
        )
    module_id = str(document.get("id") or path.parent.name)
    distribution = document.get("distribution") or {}
    package = str(distribution.get("package") or "")
    features = tuple(
        str(feature.get("id"))
        for feature in (document.get("features") or [])
        if isinstance(feature, Mapping) and feature.get("id")
    )
    return CatalogModule(
        module_id=module_id,
        name=str(document.get("name") or module_id),
        version=str((document.get("versions") or {}).get("latest") or ""),
        provider_key=_provider_key(module_id, package),
        package=package,
        classes=tuple(str(item) for item in (document.get("class") or [])),
        features=features,
    )


def _provider_key(module_id: str, package: str) -> str:
    """The provider id a routing chain names (package tail, else the module id)."""
    tail = package.rsplit(".", 1)[-1].strip() if package else ""
    return tail or module_id


def advertised_ids(catalogue: ModelCatalogue) -> Iterable[str]:
    """Convenience: the advertised ids in list order."""
    return catalogue.advertised_ids
