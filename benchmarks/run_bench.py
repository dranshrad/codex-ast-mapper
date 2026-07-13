#!/usr/bin/env python3
"""Benchmark harness for Repository Intelligence Engine metrics."""

from __future__ import annotations

import resource
import time
from pathlib import Path

from src.ast_extractor import extract_all
from src.exporters.pipeline import export_within_budget
from src.graph import build_repo_graph
from src.parser import walk_and_parse
from src.tokenizer_util import TokenBudget


def _rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports kilobytes
    if usage > 10_000_000:
        return usage / (1024 * 1024)
    return usage / 1024


def run_bench(root: Path, max_tokens: int = 8000) -> dict[str, float | int | str]:
    raw_chars = 0
    t0 = time.perf_counter()
    parsed = walk_and_parse(root)
    for p in parsed:
        raw_chars += len(p.source)
    files = extract_all(parsed)
    graph = build_repo_graph(files)
    result = export_within_budget(
        files,
        TokenBudget(max_tokens=max_tokens),
        fmt="xml",
        mode="developer",
        graph=graph,
    )
    elapsed = time.perf_counter() - t0
    files_n = max(len(parsed), 1)
    return {
        "root": str(root),
        "files": len(parsed),
        "modules": len(graph.modules),
        "edges": len(graph.edges),
        "wall_s": round(elapsed, 4),
        "files_per_s": round(files_n / elapsed, 2) if elapsed > 0 else 0,
        "tokens": result.tokens,
        "max_tokens": max_tokens,
        "raw_chars": raw_chars,
        "compression_ratio": round(raw_chars / max(result.tokens, 1), 2),
        "rss_mb": round(_rss_mb(), 2),
        "prune_level": result.prune_level,
    }


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    metrics = run_bench(root)
    headers = [
        "files",
        "modules",
        "edges",
        "wall_s",
        "files_per_s",
        "tokens",
        "compression_ratio",
        "rss_mb",
        "prune_level",
    ]
    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join("---" for _ in headers) + " |")
    print("| " + " | ".join(str(metrics[h]) for h in headers) + " |")


if __name__ == "__main__":
    main()
