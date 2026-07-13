"""Build a repository relationship graph from extracted FileMeta IR."""

from __future__ import annotations

from collections import defaultdict

from src.models import (
    ClassMeta,
    EdgeKind,
    FileMeta,
    FunctionMeta,
    GraphEdge,
    RepoGraph,
    SymbolRef,
)
from src.paths import resolve_relative_import


def _symbol_id(module_id: str, *parts: str) -> str:
    return f"{module_id}:{'.'.join(parts)}"


def _public_symbol_count(file_meta: FileMeta) -> int:
    classes = sum(1 for c in file_meta.classes if not c.is_private)
    methods = sum(1 for c in file_meta.classes for m in c.methods if not m.is_private)
    functions = sum(1 for f in file_meta.functions if not f.is_private)
    return classes + methods + functions


def _index_class_symbols(
    module_id: str,
    cls: ClassMeta,
    symbols: dict[str, SymbolRef],
    edges: list[GraphEdge],
    parent_id: str,
) -> None:
    class_id = _symbol_id(module_id, cls.name)
    symbols[class_id] = SymbolRef(
        id=class_id,
        name=cls.name,
        kind="class",
        module_id=module_id,
        is_private=cls.is_private,
    )
    edges.append(GraphEdge(source=parent_id, target=class_id, kind=EdgeKind.CONTAINS))
    edges.append(GraphEdge(source=module_id, target=class_id, kind=EdgeKind.DEFINES))

    for method in cls.methods:
        method_id = _symbol_id(module_id, cls.name, method.name)
        symbols[method_id] = SymbolRef(
            id=method_id,
            name=method.name,
            kind="method",
            module_id=module_id,
            is_private=method.is_private,
        )
        edges.append(GraphEdge(source=class_id, target=method_id, kind=EdgeKind.CONTAINS))
        edges.append(GraphEdge(source=module_id, target=method_id, kind=EdgeKind.DEFINES))
        _index_nested_functions(module_id, method, symbols, edges, method_id, cls.name)

    for nested in cls.nested_classes:
        _index_class_symbols(module_id, nested, symbols, edges, class_id)


def _index_nested_functions(
    module_id: str,
    fn: FunctionMeta,
    symbols: dict[str, SymbolRef],
    edges: list[GraphEdge],
    parent_id: str,
    *qual: str,
) -> None:
    for nested in fn.nested:
        nested_parts = (*qual, fn.name, nested.name) if qual else (fn.name, nested.name)
        # Prefer shorter id under parent
        nested_id = _symbol_id(module_id, *nested_parts)
        symbols[nested_id] = SymbolRef(
            id=nested_id,
            name=nested.name,
            kind="function",
            module_id=module_id,
            is_private=nested.is_private,
        )
        edges.append(GraphEdge(source=parent_id, target=nested_id, kind=EdgeKind.CONTAINS))
        _index_nested_functions(module_id, nested, symbols, edges, nested_id, *nested_parts)


def _resolve_import_target(
    current_module: str,
    source: str,
    modules: dict[str, FileMeta],
) -> str | None:
    candidate = resolve_relative_import(current_module, source)
    if candidate in modules:
        return candidate
    # Try package __init__ style: pkg.models may be pkg.models or just models under pkg
    if candidate:
        # Prefix match: imported "src.models" when module is "src.models"
        for mid in modules:
            if mid == candidate or mid.endswith("." + candidate):
                return mid
    return None


def _resolve_base_class(
    module_id: str,
    base: str,
    class_index: dict[str, str],
    local_classes: set[str],
) -> str | None:
    """Map a base name to a class symbol id when resolvable in-repo."""
    simple = base.split(".")[-1].split("[")[0]
    if simple in local_classes:
        return _symbol_id(module_id, simple)
    # Global simple-name index (last wins if duplicates — good enough for v0.2)
    if simple in class_index:
        return class_index[simple]
    if base in class_index:
        return class_index[base]
    return None


def build_repo_graph(files: list[FileMeta]) -> RepoGraph:
    """
    Construct a relationship graph from extracted file metadata.

    Edges: imports, inherits, contains, defines.
    Importance: degree centrality + inbound-import boost + public symbol weight.
    """
    modules: dict[str, FileMeta] = {}
    for f in files:
        mid = f.module_id or f.path
        modules[mid] = f

    symbols: dict[str, SymbolRef] = {}
    edges: list[GraphEdge] = []

    # Index class simple names for inheritance resolution
    class_index: dict[str, str] = {}
    for mid, f in modules.items():
        symbols[mid] = SymbolRef(id=mid, name=mid, kind="module", module_id=mid)
        for cls in f.classes:
            class_index[cls.name] = _symbol_id(mid, cls.name)

    for mid, f in modules.items():
        for cls in f.classes:
            _index_class_symbols(mid, cls, symbols, edges, mid)
        for fn in f.functions:
            fn_id = _symbol_id(mid, fn.name)
            symbols[fn_id] = SymbolRef(
                id=fn_id,
                name=fn.name,
                kind="function",
                module_id=mid,
                is_private=fn.is_private,
            )
            edges.append(GraphEdge(source=mid, target=fn_id, kind=EdgeKind.CONTAINS))
            edges.append(GraphEdge(source=mid, target=fn_id, kind=EdgeKind.DEFINES))
            _index_nested_functions(mid, fn, symbols, edges, fn_id)

        local_classes = {c.name for c in f.classes}
        for cls in f.classes:
            class_id = _symbol_id(mid, cls.name)
            for base in cls.bases:
                target = _resolve_base_class(mid, base, class_index, local_classes)
                if target is not None:
                    edges.append(
                        GraphEdge(
                            source=class_id,
                            target=target,
                            kind=EdgeKind.INHERITS,
                            name=base,
                        )
                    )

        for imp in f.imports:
            target = _resolve_import_target(mid, imp.source, modules)
            if target is not None:
                imp.resolved = target
                edges.append(
                    GraphEdge(
                        source=mid,
                        target=target,
                        kind=EdgeKind.IMPORTS,
                        name=",".join(imp.names) if imp.names else None,
                    )
                )

    # Importance scoring
    inbound: dict[str, int] = defaultdict(int)
    outbound: dict[str, int] = defaultdict(int)
    degree: dict[str, int] = defaultdict(int)

    for edge in edges:
        if edge.kind is EdgeKind.IMPORTS:
            outbound[edge.source] += 1
            inbound[edge.target] += 1
        if edge.source in modules:
            degree[edge.source] += 1
        if edge.target in modules:
            degree[edge.target] += 1
        # Also count class-level inherits toward owning modules
        if edge.kind is EdgeKind.INHERITS:
            src_mod = edge.source.split(":", 1)[0]
            tgt_mod = edge.target.split(":", 1)[0]
            degree[src_mod] += 1
            degree[tgt_mod] += 1

    importance: dict[str, float] = {}
    for mid, f in modules.items():
        pub = _public_symbol_count(f)
        score = (
            float(degree.get(mid, 0))
            + 2.0 * float(inbound.get(mid, 0))
            + 0.5 * float(outbound.get(mid, 0))
            + 0.25 * float(pub)
        )
        importance[mid] = score
        f.importance = score

    return RepoGraph(modules=modules, symbols=symbols, edges=edges, importance=importance)


def ranked_modules(graph: RepoGraph, *, ascending: bool = True) -> list[str]:
    """Return module ids ordered by importance (ascending = drop first)."""
    return sorted(
        graph.importance.keys(),
        key=lambda mid: (graph.importance.get(mid, 0.0), mid),
        reverse=not ascending,
    )


def top_hubs(graph: RepoGraph, n: int = 5) -> list[tuple[str, float]]:
    """Return the n highest-importance modules."""
    ranked = ranked_modules(graph, ascending=False)
    return [(mid, graph.importance[mid]) for mid in ranked[:n]]
