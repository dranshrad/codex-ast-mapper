"""JSON IR exporter — modules, symbols, edges, importance."""

from __future__ import annotations

import json
from typing import Any

from src.exporters.xml import EncodeFlags
from src.graph import build_repo_graph
from src.models import EdgeKind, FileMeta, RepoGraph


def _module_payload(f: FileMeta, flags: EncodeFlags) -> dict[str, Any]:
    classes: list[dict[str, Any]] = []
    for c in f.classes:
        if not flags.include_helpers and c.is_private:
            continue
        methods = []
        for m in c.methods:
            if not flags.include_helpers and m.is_private:
                continue
            methods.append(
                {
                    "name": m.name,
                    "args": [
                        {"name": a.name, "type": a.type_hint if flags.include_types else None}
                        for a in m.args
                    ],
                    "ret": m.return_type if flags.include_types else None,
                    "doc": m.docstring if flags.include_doc else None,
                    "async": m.is_async,
                    "generator": m.is_generator,
                }
            )
        classes.append(
            {
                "name": c.name,
                "bases": c.bases,
                "doc": c.docstring if flags.include_doc else None,
                "methods": methods,
            }
        )
    functions = []
    for fn in f.functions:
        if not flags.include_helpers and fn.is_private:
            continue
        functions.append(
            {
                "name": fn.name,
                "args": [
                    {"name": a.name, "type": a.type_hint if flags.include_types else None}
                    for a in fn.args
                ],
                "ret": fn.return_type if flags.include_types else None,
                "doc": fn.docstring if flags.include_doc else None,
            }
        )
    imports: list[dict[str, Any]] = []
    if flags.include_imports:
        for imp in f.imports:
            imports.append(
                {
                    "src": imp.resolved or imp.source,
                    "names": imp.names,
                    "relative": imp.is_relative,
                }
            )
    return {
        "id": f.module_id or f.path,
        "path": f.path,
        "lang": f.language.short,
        "importance": f.importance,
        "imports": imports,
        "classes": classes,
        "functions": functions,
    }


def export_json(
    files: list[FileMeta],
    flags: EncodeFlags | None = None,
    graph: RepoGraph | None = None,
) -> str:
    """Serialize repository IR + graph edges as compact JSON."""
    flags = flags or EncodeFlags()
    graph = graph or build_repo_graph(files)
    edge_kinds = {EdgeKind.IMPORTS, EdgeKind.INHERITS}
    if flags.include_helpers:
        edge_kinds |= {EdgeKind.CONTAINS, EdgeKind.DEFINES}

    module_ids = {f.module_id or f.path for f in files}
    edges = [
        {
            "source": e.source,
            "target": e.target,
            "kind": e.kind.value,
            "name": e.name,
        }
        for e in graph.edges
        if e.kind in edge_kinds
        and (
            e.source in module_ids
            or e.source.split(":", 1)[0] in module_ids
            or e.target in module_ids
            or e.target.split(":", 1)[0] in module_ids
        )
    ]

    payload = {
        "version": 2,
        "modules": [_module_payload(f, flags) for f in files],
        "edges": edges,
        "importance": {mid: score for mid, score in graph.importance.items() if mid in module_ids},
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
