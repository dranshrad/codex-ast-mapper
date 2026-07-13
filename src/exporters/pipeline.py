"""Budget-aware multi-format export pipeline with importance-based dropping."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace

from src.exporters.json import export_json
from src.exporters.mermaid import export_mermaid
from src.exporters.modes import mode_flags
from src.exporters.xml import EncodeFlags, export_xml
from src.graph import build_repo_graph, ranked_modules
from src.models import (
    PRUNE_ORDER,
    FileMeta,
    LLMMode,
    OutputFormat,
    PruneLevel,
    RepoGraph,
)
from src.tokenizer_util import TokenBudget


@dataclass(frozen=True, slots=True)
class ExportResult:
    content: str
    tokens: int
    prune_level: PruneLevel
    within_budget: bool
    format: OutputFormat
    mode: LLMMode


def _flags_for(level: PruneLevel, base: EncodeFlags) -> EncodeFlags:
    """Tighten base (mode) flags through the progressive prune matrix."""
    include_doc = base.include_doc
    include_helpers = base.include_helpers
    include_types = base.include_types
    include_imports = base.include_imports
    compress_types = base.compress_types

    if level in {"helpers", "docstrings", "types", "imports"}:
        include_helpers = False
    if level in {"docstrings", "types", "imports"}:
        include_doc = False
    if level in {"types", "imports"}:
        compress_types = True
    if level == "imports":
        include_imports = False
        # Planning-style: also drop types display when fully stripped
        if not base.include_types:
            include_types = False

    return EncodeFlags(
        include_doc=include_doc,
        include_types=include_types,
        include_helpers=include_helpers,
        include_imports=include_imports,
        compress_types=compress_types,
    )


def _render(
    files: list[FileMeta],
    fmt: OutputFormat,
    flags: EncodeFlags,
    graph: RepoGraph,
) -> str:
    if fmt == "json":
        return export_json(files, flags, graph=graph)
    if fmt == "mermaid":
        return export_mermaid(files, flags, graph=graph)
    return export_xml(files, flags)


def _drop_lowest_importance(
    files: list[FileMeta],
    graph: RepoGraph,
) -> list[FileMeta]:
    """Drop the lowest-importance module still present."""
    if len(files) <= 1:
        return files
    present = {f.module_id or f.path for f in files}
    ordered = [mid for mid in ranked_modules(graph, ascending=True) if mid in present]
    if not ordered:
        # Fallback: drop last by path
        drop = sorted(files, key=lambda f: f.path)[0].path
        return [f for f in files if f.path != drop]
    drop_mid = ordered[0]
    return [f for f in files if (f.module_id or f.path) != drop_mid]


def export_within_budget(
    files: list[FileMeta],
    budget: TokenBudget,
    *,
    fmt: OutputFormat = "xml",
    mode: LLMMode = "developer",
    graph: RepoGraph | None = None,
) -> ExportResult:
    """
    Export through prune tiers, then drop low-centrality modules until under budget.
    """
    working = deepcopy(files)
    graph = graph or build_repo_graph(working)
    base = mode_flags(mode)
    levels: tuple[PruneLevel, ...] = ("none", *PRUNE_ORDER)

    for level in levels:
        flags = _flags_for(level, base) if level != "none" else base
        # At "none", still apply mode base as-is
        if level == "none":
            flags = base
        content = _render(working, fmt, flags, graph)
        tokens = budget.measure(content)
        if tokens <= budget.max_tokens:
            return ExportResult(
                content=content,
                tokens=tokens,
                prune_level=level,
                within_budget=True,
                format=fmt,
                mode=mode,
            )

    flags = _flags_for("imports", base)
    while len(working) > 1:
        working = _drop_lowest_importance(working, graph)
        # Rebuild subgraph importance view for remaining modules
        sub = build_repo_graph(working)
        content = _render(working, fmt, flags, sub)
        tokens = budget.measure(content)
        if tokens <= budget.max_tokens:
            return ExportResult(
                content=content,
                tokens=tokens,
                prune_level="imports",
                within_budget=True,
                format=fmt,
                mode=mode,
            )

    content = _render(working, fmt, flags, build_repo_graph(working))
    tokens = budget.measure(content)
    return ExportResult(
        content=content,
        tokens=tokens,
        prune_level="imports",
        within_budget=tokens <= budget.max_tokens,
        format=fmt,
        mode=mode,
    )


def merge_flags(base: EncodeFlags, **overrides: bool) -> EncodeFlags:
    """Return a copy of ``base`` with selected fields overridden."""
    return replace(base, **overrides)
