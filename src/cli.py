"""Typer CLI for Codex-AST Repo Mapper."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from src import __version__
from src.ast_extractor import extract_all
from src.encoder import encode_within_budget
from src.models import Language
from src.parser import ensure_vendor_dir, walk_and_parse
from src.tokenizer_util import TokenBudget

console = Console(stderr=True)


class LangChoice(str, Enum):
    python = "python"
    typescript = "typescript"
    go = "go"
    all = "all"


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
        help="Hard token budget for the emitted XML map (tiktoken cl100k_base).",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write XML to this path instead of stdout.",
    ),
    workers: int | None = typer.Option(
        None,
        "--workers",
        "-w",
        min=1,
        help="Thread pool size for concurrent parsing (default: auto).",
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
    """Walk a directory, extract AST structure, and emit a pruned XML map."""
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
    budget = TokenBudget(max_tokens=max_tokens)
    result = encode_within_budget(files, budget)

    if output is not None:
        output.write_text(result.xml + "\n", encoding="utf-8")
    else:
        typer.echo(result.xml)

    if quiet:
        if not result.within_budget:
            raise typer.Exit(code=2)
        return

    table = Table(title="Codex-AST Repo Mapper", show_header=True, header_style="bold")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Version", __version__)
    table.add_row("Root", str(dir))
    table.add_row("Files parsed", str(len(parsed)))
    table.add_row("Files in map", str(result.xml.count("<file ")))
    table.add_row("Tokens", f"{result.tokens} / {max_tokens}")
    table.add_row("Prune level", result.prune_level)
    table.add_row("Within budget", "yes" if result.within_budget else "NO")
    if errors:
        table.add_row("Parse errors", str(len(errors)))
    console.print(table)

    if not result.within_budget:
        raise typer.Exit(code=2)


app = typer.Typer(
    name="codex-ast-mapper",
    help="Map a repository into a hyper-dense, token-budgeted XML AST summary for LLMs.",
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
)
app.callback()(map_repo)


if __name__ == "__main__":
    app()
