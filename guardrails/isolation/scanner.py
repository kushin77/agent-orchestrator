"""AST-based tenant-isolation anti-pattern scanner (issue #30).

Detect-first / report-only: this scanner **never mutates anything**.  It
walks Python source (stdlib ``ast``, fully offline) and models the
*tenant-store surfaces* of a module — the entity indexes that hold tenant
records — then checks every access path on those indexes for the documented
anti-patterns (the ``capital-underwriting`` account-isolation shape and the
``saas-rbac`` #107 scope-separate doctrine):

``R1 SCOPE-DROP-READ`` (high)
    A tenant-dimensioned index is *read* with a key that drops the tenant
    dimension (``self._agents.get(agent_id)`` on an index whose first
    dimension is the tenant).  A cross-tenant read vector.

``R2 SCOPE-DROP-WRITE`` (critical)
    A tenant-dimensioned index is *written/deleted* with a key that drops the
    tenant dimension (``self._agents[agent_id] = ...``).  A cross-tenant
    write vector — the worst class.

``R3 CROSS-TENANT-FALLBACK`` (high)
    A tenant-dimensioned index is read with ``.get(non_tenant_key, default)``
    — a fallback that can resolve outside the caller's tenant.

``R4 SHARED-MUTABLE-STATE`` (high)
    A module-level mutable container is mutated at runtime (shared across
    every tenant in the process) and is not tenant-dimensioned, in a module
    that is otherwise tenant-aware.  Shared, unkeyed, mutable state.

Model and honest tri-state verdicts
-----------------------------------
An *entity index* is a dict-typed attribute (``self._x = {}``, a dataclass
``x: dict = field(default_factory=dict)``, or a module-level ``_X = {}``)
that is accessed with at least one *non-constant* key (a constant-key-only
dict is a stats/config map, not a record store, and is ignored).

An index is *tenant-dimensioned* when at least one write keys it with a
tenant reference (a name or attribute whose text contains ``tenant`` — the
``tenant_id`` parameter, ``agent.tenant_id``, ``kb.tenant_id``).  For a
tenant-dimensioned index every access must carry that dimension.

* ``OK``            every access on a tenant-dimensioned index carries the
                    tenant dimension (no scope drops / fallbacks).
* ``NOT-OK``        a scope drop / fallback / shared-state pattern fired —
                    concrete finding with evidence.
* ``CANNOT-ASSESS`` an entity index is keyed only by non-tenant identifiers
                    (an id-keyed store bound to one tenant object, or keys
                    derived through a composite-id function).  Tenant
                    scoping cannot be proven or refuted from structure alone.
                    Enumerated in the report; never reads as a pass.

Deterministic ordering: findings and verdicts sort by file, owner, line.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .errors import ScanError
from .model import (Finding, FindingCategory, IndexVerdict, ScanReport,
                    Severity)
from .tristate import TriState

# Rule identifiers (consumed by the triage gate in triage.py).
RULE_SCOPE_DROP_READ = "R1-SCOPE-DROP-READ"
RULE_SCOPE_DROP_WRITE = "R2-SCOPE-DROP-WRITE"
RULE_CROSS_TENANT_FALLBACK = "R3-CROSS-TENANT-FALLBACK"
RULE_SHARED_MUTABLE_STATE = "R4-SHARED-MUTABLE-STATE"

# dict-like callable names accepted as index initializers.
_DICT_CALL_NAMES = {
    "dict", "defaultdict", "OrderedDict", "Counter", "ChainMap", "Mapping",
    "MutableMapping",
}
# dict-like type names accepted as annotations.
_DICT_TYPE_NAMES = {"dict", "Dict", "Mapping", "MutableMapping", "defaultdict"}

# Methods that read an index.
_READ_METHODS = {"get", "getitem", "contains", "__contains__", "__getitem__",
                 "items", "keys", "values", "copy"}
# Methods that mutate an index (write).
_WRITE_METHODS = {"setdefault", "update", "pop", "popitem", "__setitem__",
                  "__delitem__", "clear", "store", "append", "add"}


# --------------------------------------------------------------------------- #
# tenant-reference helpers
# --------------------------------------------------------------------------- #

def _is_tenant_identity(text: Optional[str]) -> bool:
    """True when an identifier names a *tenant identity*, not a catalog key.

    ``tenant_id``, ``tenant``, ``_tenant_id`` name an actual tenant
    namespace.  ``tenant_type`` (``startup``/``smb``/...) is a pack/catalog
    key, not a tenant identity, and must not be treated as one (the
    identity/rbac preset catalog keys its registry by tenant_type).
    """
    name = (text or "").lower()
    if not name:
        return False
    if "tenant_type" in name or name.endswith("_type"):
        return False
    return "tenant" in name


def _is_tenant_ref(node: Optional[ast.expr]) -> bool:
    """True when ``node`` textually references a tenant entity.

    A name whose identifier is a tenant identity (``tenant_id``, ``tenant``)
    or an attribute whose name is a tenant identity (``agent.tenant_id``,
    ``kb.tenant_id``, ``self._tenant_id``).  Catalog keys such as
    ``tenant_type`` are excluded.  Also recurses so ``obj.tenant.id``-style
    chains still count.
    """
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return _is_tenant_identity(node.id)
    if isinstance(node, ast.Attribute):
        if _is_tenant_identity(node.attr):
            return True
        return _is_tenant_ref(node.value)
    if isinstance(node, ast.Tuple):
        return any(_is_tenant_ref(elt) for elt in node.elts)
    if isinstance(node, ast.Call):
        # A call is a tenant reference only when one of its own arguments is;
        # otherwise derived-id calls (memory_id, container_id) do not count.
        return any(_is_tenant_ref(a) for a in node.args)
    return False


def _key_is_constant(key: Optional[ast.expr]) -> bool:
    """True when ``key`` is a compile-time constant (literal only)."""
    if key is None:
        return True
    if isinstance(key, ast.Constant):
        return True
    if isinstance(key, (ast.Tuple, ast.List)):
        return all(_key_is_constant(e) for e in key.elts)
    return False


# --------------------------------------------------------------------------- #
# access-site collection
# --------------------------------------------------------------------------- #

@dataclass
class AccessSite:
    """One read/write/delete/membership site on an index."""

    kind: str  # "read" | "write" | "delete" | "member"
    key: Optional[ast.expr]
    node: ast.AST
    method: str = ""
    has_default: bool = False  # .get(k, default) fallback present
    in_class: str = ""
    owner: str = ""  # "self.<attr>" or module name
    method_call: str = ""  # e.g. ".get" / ".setdefault" when a method call
    from_tenant_method: bool = False  # reached from a tenant-scoped method


def _enclosing_names(tree: ast.AST) -> Dict[int, Tuple[str, str]]:
    """Map node-id -> (class_name, method_name) for scoped naming."""
    mapping: Dict[int, Tuple[str, str]] = {}

    def _walk(body: Iterable[ast.stmt], cls: str, meth: str) -> None:
        for node in body:
            mapping[id(node)] = (cls, meth)
            children = list(ast.iter_child_nodes(node))
            for child in children:
                mapping[id(child)] = (cls, meth)
                _deep(child, cls, meth)

    def _deep(node: ast.AST, cls: str, meth: str) -> None:
        for child in ast.iter_child_nodes(node):
            mapping[id(child)] = (cls, meth)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _walk(list(child.body), cls, child.name)
            elif isinstance(child, (ast.If, ast.For, ast.While, ast.With,
                                    ast.Try)):
                _deep(child, cls, meth)

    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef,)):
            for item in node.body:
                mapping[id(item)] = (node.name, "")
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _walk(list(item.body), node.name, item.name)
                else:
                    for child in ast.iter_child_nodes(item):
                        mapping[id(child)] = (node.name, "")
    return mapping


def _index_owner_exprs(index_attr: str) -> Tuple[object, ...]:
    """Return matchers for ``self.<attr>`` attribute accesses."""
    return (_SelfAttr(index_attr),)


class _SelfAttr:
    """Matches an ``Attribute(value=Name('self'), attr=<name>)`` node."""

    __slots__ = ("attr",)

    def __init__(self, attr: str) -> None:
        self.attr = attr

    def matches(self, node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr == self.attr
        )


def _collect_accesses(
    node: ast.AST,
    attr_matcher: "_SelfAttr",
    names: Dict[int, Tuple[str, str]],
) -> List[AccessSite]:
    """Collect every access site on ``self.<attr>`` inside ``node``."""
    sites: List[AccessSite] = []

    for sub in ast.walk(node):
        cls, meth = names.get(id(sub), ("", ""))
        # -- Subscript: self.attr[key] ... -------------------------------
        if isinstance(sub, ast.Subscript) and attr_matcher.matches(sub.value):
            key = sub.slice
            kind = "read"
            parent = _parent_of(node, sub)
            if parent is not None and _is_write_target(sub, parent):
                kind = "write"
            sites.append(AccessSite(
                kind=kind, key=key, node=sub, method=meth,
                in_class=cls, owner=attr_matcher.attr,
            ))
            continue
        # -- Method call: self.attr.get(...) / .setdefault(...) ----------
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if not attr_matcher.matches(sub.func.value):
                continue
            mname = sub.func.attr
            if mname in _WRITE_METHODS or mname in _READ_METHODS:
                kind = "delete" if mname in {"pop", "popitem", "clear",
                                             "__delitem__"} else (
                    "write" if mname in _WRITE_METHODS else "read")
                key = sub.args[0] if sub.args else None
                has_default = mname == "get" and len(sub.args) >= 2
                sites.append(AccessSite(
                    kind=kind, key=key, node=sub, method=meth,
                    has_default=has_default, in_class=cls,
                    owner=attr_matcher.attr, method_call="." + mname,
                ))
    return sites


def _parent_of(root: ast.AST, target: ast.AST) -> Optional[ast.AST]:
    for parent in ast.walk(root):
        for child in ast.iter_child_nodes(parent):
            if child is target:
                return parent
    return None


def _is_write_target(sub: ast.Subscript, parent: ast.AST) -> bool:
    """True when the subscript is being assigned into (a write)."""
    if isinstance(parent, ast.Assign):
        return any(_contains_node(t, sub) for t in parent.targets)
    if isinstance(parent, ast.AugAssign):
        return _contains_node(parent.target, sub)
    if isinstance(parent, ast.Delete):
        return _contains_node(parent, sub)
    return False


def _contains_node(container: ast.AST, target: ast.AST) -> bool:
    for node in ast.walk(container):
        if node is target:
            return True
    return False


# --------------------------------------------------------------------------- #
# index discovery
# --------------------------------------------------------------------------- #

@dataclass
class Index:
    """A discovered entity index and its collected access sites."""

    owner: str  # "Class.attr" or "<module>.NAME"
    attr: str
    cls: str  # class name or "<module>"
    sites: List[AccessSite] = field(default_factory=list)

    # Derived state filled by the analyzer.
    tenant_keyed_writes: int = 0
    tenant_dimensioned: bool = False


def _is_dict_literal(value: Optional[ast.expr]) -> bool:
    if isinstance(value, ast.Dict):
        return True
    if isinstance(value, ast.Call):
        func = value.func
        name = ""
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        return name in _DICT_CALL_NAMES
    return False


def _is_dict_annotation(ann: Optional[ast.expr]) -> bool:
    if ann is None:
        return False
    if isinstance(ann, ast.Name):
        return ann.id in _DICT_TYPE_NAMES
    if isinstance(ann, ast.Subscript):
        return _is_dict_annotation(ann.value)
    if isinstance(ann, ast.Attribute):
        return ann.attr in _DICT_TYPE_NAMES
    return False


def _self_assignments_in_class(cls: ast.ClassDef) -> set:
    """Collect ``self.<attr>`` names assigned dict-like values in the class."""
    attrs: set = set()
    for node in ast.walk(cls):
        # self.attr = <dict-like>
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and \
                        isinstance(target.value, ast.Name) and \
                        target.value.id == "self":
                    if _is_dict_literal(node.value):
                        attrs.add(target.attr)
        # self.attr: T = <dict-like>
        if isinstance(node, ast.AnnAssign) and node.target is not None \
                and isinstance(node.target, ast.Attribute) and \
                isinstance(node.target.value, ast.Name) and \
                node.target.value.id == "self":
            if node.value is not None and _is_dict_literal(node.value):
                attrs.add(node.target.attr)
    return attrs


def _dataclass_dict_fields(cls: ast.ClassDef) -> set:
    """Collect class-body dict-typed annotations (dataclass fields)."""
    fields: set = set()
    for item in cls.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            if _is_dict_annotation(item.annotation):
                fields.add(item.target.id)
    return fields


def _tenant_context_ids(tree: ast.AST) -> set:
    """Node ids that sit inside a function with a tenant-named parameter.

    A tenant-scoped method is one that names its tenant (``tenant_id``,
    ``tenant``, ...) in its signature; module-global state reached from such
    a method is shared mutable state across tenant namespaces.
    """
    marked: set = set()

    def _params(node: ast.AST) -> List[str]:
        args = getattr(node, "args", None)
        if args is None:
            return []
        positional = list(getattr(args, "posonlyargs", [])) + \
            list(getattr(args, "args", [])) + \
            list(getattr(args, "kwonlyargs", []))
        names = [a.arg for a in positional if hasattr(a, "arg")]
        if getattr(args, "vararg", None) is not None:
            names.append(args.vararg.arg)
        if getattr(args, "kwarg", None) is not None:
            names.append(args.kwarg.arg)
        return names

    def _walk(node: ast.AST, body: Iterable[ast.stmt]) -> None:
        for stmt in body:
            for sub in ast.walk(stmt):
                marked.add(id(sub))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(_is_tenant_identity(p) for p in _params(node)):
                _walk(node, list(node.body))
    return marked


def _module_container_names(tree: ast.Module) -> dict:
    """Module-level ``NAME = <mutable>`` definitions (dict/list/set)."""
    containers: dict = {}
    for node in tree.body:
        targets: List[ast.expr] = []
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node.target, ast.Name):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    value = node.value
                    if _is_dict_literal(value):
                        containers[target.id] = "dict"
                    elif isinstance(value, (ast.List, ast.Set)) and \
                            (isinstance(node, ast.Assign) and node.value is value):
                        containers[target.id] = "list" if isinstance(
                            value, ast.List) else "set"
    return containers


# --------------------------------------------------------------------------- #
# module analysis
# --------------------------------------------------------------------------- #

@dataclass
class ModuleScan:
    """Analysis of one Python module."""

    path: str
    source_lines: List[str]
    indexes: List[Index] = field(default_factory=list)
    module_containers: Dict[str, str] = field(default_factory=dict)
    mentions_tenant: bool = False
    findings: List[Finding] = field(default_factory=list)
    verdicts: List[IndexVerdict] = field(default_factory=list)
    is_target: bool = False


def _snippet(lines: List[str], node: ast.AST) -> str:
    start = max(1, getattr(node, "lineno", 1))
    end = min(len(lines), getattr(node, "end_lineno", start) or start)
    return "\n".join(lines[start - 1:end])


def analyze_module(path: str, source: str, base: str = "") -> ModuleScan:
    """Analyze one module's tenant-store surfaces.  Read-only, deterministic."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:  # pragma: no cover - defensive
        raise ScanError(f"cannot parse {path}: {exc}") from exc

    rel = os.path.relpath(path, base) if base else path
    lines = source.splitlines()
    scan = ModuleScan(path=rel, source_lines=lines)
    scan.mentions_tenant = "tenant" in source.lower()
    names = _enclosing_names(tree)

    # -- class entity indexes ---------------------------------------------
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        attrs = _self_assignments_in_class(cls) | _dataclass_dict_fields(cls)
        for attr in sorted(attrs):
            matcher = _SelfAttr(attr)
            sites = _collect_accesses(cls, matcher, names)
            non_constant = [s for s in sites if not _key_is_constant(s.key)]
            if not non_constant:
                continue  # constant-key-only: stats/config map, not a record
            idx = Index(owner=f"{cls.name}.{attr}", attr=attr, cls=cls.name,
                        sites=sites)
            scan.indexes.append(idx)

    # -- module-level mutable containers ----------------------------------
    containers = _module_container_names(tree)
    scan.module_containers = containers
    tenant_ids = _tenant_context_ids(tree)
    if containers:
        for name in sorted(containers):
            sites = _collect_module_container_sites(tree, name, tenant_ids)
            if not sites:
                continue  # defined but never accessed via the container
            idx = Index(owner=f"<module>.{name}", attr=name, cls="<module>",
                        sites=sites)
            scan.indexes.append(idx)

    # -- classify ---------------------------------------------------------
    for idx in scan.indexes:
        _classify_index(idx, scan)
    scan.is_target = bool(scan.indexes) or bool(scan.verdicts)
    return scan


def _is_index_owned(idx: Index) -> bool:
    """True when the class/module mutates the index (write/delete site).

    A pure-read mapping (config or an injected store) is not a store surface
    this class owns, so it is not modeled as an entity index.
    """
    return any(s.kind in ("write", "delete") for s in idx.sites)


def _collect_module_container_sites(tree: ast.Module, name: str,
                                    tenant_ids: Optional[set] = None,
                                    ) -> List[AccessSite]:
    """Access sites on a module-level container by bare ``Name``/attribute."""
    tenant_ids = tenant_ids or set()
    sites: List[AccessSite] = []
    for sub in ast.walk(tree):
        # Subscript mutation or read: _NAME[key]
        if isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name) \
                and sub.value.id == name:
            parent = _parent_of(tree, sub)
            kind = "read"
            if parent is not None and _is_write_target(sub, parent):
                kind = "write"
            sites.append(AccessSite(
                kind=kind, key=sub.slice, node=sub,
                owner="<module>." + name,
                from_tenant_method=id(sub) in tenant_ids,
            ))
            continue
        # Method call: _NAME.get(...) / _NAME.append(...)
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                and isinstance(sub.func.value, ast.Name) \
                and sub.func.value.id == name:
            mname = sub.func.attr
            if mname in _WRITE_METHODS or mname in _READ_METHODS:
                kind = "write" if mname in _WRITE_METHODS else "read"
                if mname in {"pop", "clear", "__delitem__"}:
                    kind = "delete"
                key = sub.args[0] if sub.args else None
                has_default = mname == "get" and len(sub.args) >= 2
                sites.append(AccessSite(
                    kind=kind, key=key, node=sub, has_default=has_default,
                    owner="<module>." + name, method_call="." + mname,
                    from_tenant_method=id(sub) in tenant_ids,
                ))
    return sites


# --------------------------------------------------------------------------- #
# index classification (the rules)
# --------------------------------------------------------------------------- #

def _classify_index(idx: Index, scan: ModuleScan) -> None:
    if not _is_index_owned(idx):
        # Pure-read mapping (config map or injected store): not a store
        # surface this class owns, so there is nothing to assess here.
        return
    tenant_ref_writes = 0
    for site in idx.sites:
        if site.kind in ("write", "delete") and _is_tenant_ref(site.key):
            tenant_ref_writes += 1
    idx.tenant_keyed_writes = tenant_ref_writes
    idx.tenant_dimensioned = tenant_ref_writes > 0

    if not idx.tenant_dimensioned:
        # id-keyed (single-tenant-object) or composite derived keys; not
        # provable as a cross-tenant surface from structure alone.
        if idx.cls == "<module>":
            if scan.mentions_tenant:
                _emit_shared_state(idx, scan)
            else:
                scan.verdicts.append(IndexVerdict(
                    verdict=TriState.CANNOT_ASSESS, target=scan.path,
                    owner=idx.owner,
                    reason="module-level mutable container in a non-tenant "
                           "module; not a tenant isolation surface",
                ))
        else:
            scan.verdicts.append(IndexVerdict(
                verdict=TriState.CANNOT_ASSESS, target=scan.path,
                owner=idx.owner,
                reason="entity index keyed only by non-tenant identifiers "
                       "(single-tenant-object or composite derived keys); "
                       "tenant scoping not provable statically",
            ))
        return

    # Tenant-dimensioned: every access must carry the tenant dimension.
    dropped: List[AccessSite] = []
    for site in idx.sites:
        if _key_is_constant(site.key):
            continue
        if not _is_tenant_ref(site.key):
            dropped.append(site)

    if not dropped:
        scan.verdicts.append(IndexVerdict(
            verdict=TriState.OK, target=scan.path, owner=idx.owner,
            reason="tenant-dimensioned entity index; every access carries "
                   "the tenant dimension (no scope drops / fallbacks)",
        ))
        return

    scan.verdicts.append(IndexVerdict(
        verdict=TriState.NOT_OK, target=scan.path, owner=idx.owner,
        reason=f"{len(dropped)} access path(s) drop the tenant dimension",
    ))
    for site in dropped:
        _emit_scope_drop(idx, site, scan)


def _emit_scope_drop(idx: Index, site: AccessSite, scan: ModuleScan) -> None:
    if site.kind in ("write", "delete"):
        rule, cat, sev = RULE_SCOPE_DROP_WRITE, \
            FindingCategory.SCOPE_DROP_WRITE, Severity.CRITICAL
        verb = "writes to" if site.kind == "write" else "deletes from"
    else:
        if site.method_call == ".get" and site.has_default:
            rule, cat, sev = RULE_CROSS_TENANT_FALLBACK, \
                FindingCategory.CROSS_TENANT_FALLBACK, Severity.HIGH
        else:
            rule, cat, sev = RULE_SCOPE_DROP_READ, \
                FindingCategory.SCOPE_DROP_READ, Severity.HIGH
        verb = "reads"
    scope = f"{site.in_class}.{site.method}" if site.in_class else site.method
    scan.findings.append(Finding(
        rule_id=rule, category=cat, severity=sev,
        message=(f"{idx.owner} is tenant-dimensioned but one path {verb} the "
                 f"index keyed without the tenant dimension"),
        target=scan.path, scope=scope.strip("."),
        line=getattr(site.node, "lineno", 0),
        evidence=_snippet(scan.source_lines, site.node),
        fix=("key every access by the tenant dimension first (denormalized "
             "tenant_id + store-layer scope gate); never look a row up by its "
             "inner id alone"),
    ))


def _emit_shared_state(idx: Index, scan: ModuleScan) -> None:
    """R4: module-level mutable container reached from tenant-scoped methods.

    Fires only when the process-global is written AND read/written from a
    method that names its tenant — the actual shared-mutable-state-across-
    tenant-namespaces vector.  A global catalog mutated only by plain
    (non-tenant-scoped) functions (e.g. ``register_pack`` in identity/rbac)
    is not provably a cross-tenant surface and reads CANNOT-ASSESS instead.
    """
    mutated = any(s.kind in ("write", "delete") for s in idx.sites)
    tenant_context = any(s.from_tenant_method for s in idx.sites)
    if not (mutated and tenant_context):
        scan.verdicts.append(IndexVerdict(
            verdict=TriState.CANNOT_ASSESS, target=scan.path, owner=idx.owner,
            reason="module-level mutable container mutated only outside "
                   "tenant-scoped methods (catalog-style registry); not "
                   "provably a shared tenant-isolation surface",
        ))
        return
    first = next((s for s in idx.sites if s.kind in ("write", "delete")), None)
    scan.verdicts.append(IndexVerdict(
        verdict=TriState.NOT_OK, target=scan.path, owner=idx.owner,
        reason="module-level mutable container reached from tenant-scoped "
               "methods and not tenant-dimensioned",
    ))
    scan.findings.append(Finding(
        rule_id=RULE_SHARED_MUTABLE_STATE,
        category=FindingCategory.SHARED_MUTABLE_STATE,
        severity=Severity.HIGH,
        message=(f"{idx.owner} is a module-level mutable container reached "
                 f"from tenant-scoped methods and not tenant-dimensioned — "
                 f"shared mutable state across tenant namespaces"),
        target=scan.path, scope="<module>",
        line=getattr(first.node, "lineno", 0) if first else 0,
        evidence=_snippet(scan.source_lines, first.node) if first else "",
        fix=("move per-tenant state into a tenant-keyed store instance "
             "(never a process-global); key every row by denormalized "
             "tenant_id"),
    ))


# --------------------------------------------------------------------------- #
# entry points
# --------------------------------------------------------------------------- #

def scan_file(path: str, source: Optional[str] = None,
              base: str = "") -> ModuleScan:
    """Scan a single Python file.  Read-only."""
    if source is None:
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
    return analyze_module(path, source, base=base)


def scan_paths(paths: Iterable[str], base: str = "",
               follow_dirs: bool = True,
               exclude_dirs: Optional[Iterable[str]] = None) -> ScanReport:
    """Scan files/directories, aggregating findings and verdicts.

    ``exclude_dirs`` names directory basenames to prune while walking (the
    self-check excludes ``tests`` — test code deliberately probes isolation
    boundaries — and ``__pycache__``).  Ordering is deterministic: files sort
    by path; findings and verdicts are appended in file order and each module
    emits them in source order.
    """
    report = ScanReport()
    excluded = set(exclude_dirs or ())
    files: List[str] = []
    for path in paths:
        if os.path.isdir(path):
            if not follow_dirs:
                continue
            for root, dirs, names in os.walk(path):
                dirs[:] = [d for d in dirs if d not in excluded]
                for name in sorted(names):
                    if name.endswith(".py"):
                        files.append(os.path.join(root, name))
        elif path.endswith(".py"):
            files.append(path)
    files = sorted(set(files))

    for path in files:
        try:
            module = scan_file(path, base=base)
        except (ScanError, OSError, UnicodeDecodeError) as exc:
            # A file we cannot parse must never read as a pass; surface it as
            # an unassessable target so the gate cannot false-green.
            report.verdicts.append(IndexVerdict(
                verdict=TriState.CANNOT_ASSESS, target=path,
                reason=f"unscannable ({exc})",
            ))
            continue
        report.files_scanned.append(module.path)
        report.findings.extend(module.findings)
        report.verdicts.extend(module.verdicts)
        if not module.is_target:
            report.files_not_targets.append(module.path)

    report.findings.sort(key=lambda f: (f.target, f.line, f.rule_id))
    report.verdicts.sort(key=lambda v: (v.target, v.owner))
    return report
