"""Mermaid diagram exporter for module / inheritance graphs."""

from __future__ import annotations

from src.exporters.xml import EncodeFlags
from src.graph import build_repo_graph
from src.models import EdgeKind, FileMeta, RepoGraph


def _safe_id(value: str) -> str:
    """Mermaid node id: alphanumeric + underscore."""
    out = []
    for ch in value:
        if ch.isalnum():
            out.append(ch)
        else:
            out.append("_")
    return "".join(out) or "node"


def export_mermaid(
    files: list[FileMeta],
    flags: EncodeFlags | None = None,
    graph: RepoGraph | None = None,
) -> str:
    """
    Emit a Mermaid flowchart of module imports and class inheritance.

    Planning mode leans on this as the primary compact artifact.
    """
    flags = flags or EncodeFlags()
    graph = graph or build_repo_graph(files)
    module_ids = {f.module_id or f.path for f in files}

    lines: list[str] = ["flowchart LR"]

    # Module nodes (importance-annotated when available)
    for f in files:
        mid = f.module_id or f.path
        nid = _safe_id(mid)
        score = graph.importance.get(mid, f.importance)
        label = f"{mid}\\nimp:{score:.1f}" if score else mid
        lines.append(f'  {nid}["{label}"]')

    # Import edges between modules
    seen: set[tuple[str, str, str]] = set()
    for edge in graph.edges:
        if edge.kind is EdgeKind.IMPORTS:
            if edge.source not in module_ids or edge.target not in module_ids:
                continue
            key = (edge.source, edge.target, "imports")
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"  {_safe_id(edge.source)} --> {_safe_id(edge.target)}")

    # Inheritance as dotted edges between class nodes
    for edge in graph.edges:
        if edge.kind is not EdgeKind.INHERITS:
            continue
        src_mod = edge.source.split(":", 1)[0]
        tgt_mod = edge.target.split(":", 1)[0]
        if src_mod not in module_ids and tgt_mod not in module_ids:
            continue
        src_name = edge.source.split(":")[-1]
        tgt_name = edge.target.split(":")[-1]
        sid = _safe_id(edge.source)
        tid = _safe_id(edge.target)
        lines.append(f'  {sid}["{src_name}"] -.->|{edge.name or "extends"}| {tid}["{tgt_name}"]')

    if len(lines) == 1:
        lines.append('  empty["(no edges)"]')
    return "\n".join(lines) + "\n"
