"""Typer CLI for the Repository Intelligence Engine."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from src import __version__
from src.ast_extractor import extract_all
from src.exporters.pipeline import export_within_budget
from src.graph import build_repo_graph, top_hubs
from src.models import Language
from src.parser import ensure_vendor_dir, walk_and_parse
from src.tokenizer_util import TokenBudget

console = Console(stderr=True)


class LangChoice(str, Enum):
    python = "python"
    typescript = "typescript"
    go = "go"
    all = "all"


class FormatChoice(str, Enum):
    xml = "xml"
    json = "json"
    mermaid = "mermaid"


class ModeChoice(str, Enum):
    developer = "developer"
    review = "review"
    planning = "planning"
    docs = "docs"
    refactor = "refactor"


def _resolve_languages(choice: LangChoice) -> set[Language] | None:
    if choice is LangChoice.all:
        return None
    return {Language(choice.value)}


def map_repo(
    dir: Path = typer.Option(
        Path("."),
        "--dir",
        "-d",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Repository root to walk.",
    ),
    lang: LangChoice = typer.Option(
        LangChoice.all,
        "--lang",
        "-l",
        help="Language filter: python | typescript | go | all.",
    ),
    max_tokens: int = typer.Option(
        8000,
        "--max-tokens",
        "-m",
        min=64,
        help="Hard token budget for the emitted artifact (tiktoken cl100k_base).",
    ),
    fmt: FormatChoice = typer.Option(
        FormatChoice.xml,
        "--format",
        "-f",
        help="Output format: xml | json | mermaid.",
    ),
    mode: ModeChoice = typer.Option(
        ModeChoice.developer,
        "--mode",
        help="LLM workflow mode: developer | review | planning | docs | refactor.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write artifact to this path instead of stdout.",
    ),
    workers: int | None = typer.Option(
        None,
        "--workers",
        "-w",
        min=1,
        help="Thread pool size for concurrent parsing (default: auto).",
    ),
    graph_stats: bool = typer.Option(
        False,
        "--graph-stats",
        help="Print module/edge counts and top hubs on stderr.",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress diagnostic summary on stderr.",
    ),
    version: bool = typer.Option(
        False,
        "--version",
        help="Print version and exit.",
    ),
) -> None:
    """Walk a repository, build a semantic graph, and emit a budgeted artifact."""
    if version:
        typer.echo(__version__)
        raise typer.Exit(code=0)

    ensure_vendor_dir()
    languages = _resolve_languages(lang)

    errors: list[tuple[Path, Exception]] = []

    def on_error(path: Path, exc: Exception) -> None:
        errors.append((path, exc))

    parsed = walk_and_parse(dir, languages, max_workers=workers, on_error=on_error)
    files = extract_all(parsed)
    graph = build_repo_graph(files)
    budget = TokenBudget(max_tokens=max_tokens)
    result = export_within_budget(
        files,
        budget,
        fmt=fmt.value,
        mode=mode.value,
        graph=graph,
    )

    if output is not None:
        output.write_text(result.content + "\n", encoding="utf-8")
    else:
        typer.echo(result.content)

    if quiet and not graph_stats:
        if not result.within_budget:
            raise typer.Exit(code=2)
        return

    if not quiet:
        table = Table(
            title="Repository Intelligence Engine",
            show_header=True,
            header_style="bold",
        )
        table.add_column("Metric")
        table.add_column("Value", justify="right")
        table.add_row("Version", __version__)
        table.add_row("Root", str(dir))
        table.add_row("Format", result.format)
        table.add_row("Mode", result.mode)
        table.add_row("Files parsed", str(len(parsed)))
        table.add_row("Modules in graph", str(len(graph.modules)))
        table.add_row("Edges", str(len(graph.edges)))
        if result.format == "xml":
            table.add_row("Modules in map", str(result.content.count("<m ")))
        table.add_row("Tokens", f"{result.tokens} / {max_tokens}")
        table.add_row("Prune level", result.prune_level)
        table.add_row("Within budget", "yes" if result.within_budget else "NO")
        if errors:
            table.add_row("Parse errors", str(len(errors)))
        console.print(table)

    if graph_stats:
        hubs = top_hubs(graph, n=5)
        hub_table = Table(title="Top hubs (importance)", show_header=True)
        hub_table.add_column("Module")
        hub_table.add_column("Score", justify="right")
        for mid, score in hubs:
            hub_table.add_row(mid, f"{score:.2f}")
        console.print(hub_table)

    if not result.within_budget:
        raise typer.Exit(code=2)


app = typer.Typer(
    name="codex-ast-mapper",
    help=(
        "Repository Intelligence Engine — structural AST maps, dependency graphs, "
        "and token-budgeted LLM context packs."
    ),
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
)
app.callback()(map_repo)


if __name__ == "__main__":
    app()
