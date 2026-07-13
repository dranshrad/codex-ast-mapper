# Codex-AST Repo Mapper

High-performance Python CLI that walks a repository, parses **Python**, **TypeScript**, and **Go** with [Tree-sitter](https://tree-sitter.github.io/tree-sitter/), extracts structural metadata (classes, inheritance, imports, signatures — **not** function bodies), and emits a **minified XML map** sized to a Tiktoken token budget.

Licensed under **GNU GPL v3**.

## Install

```bash
pipx install poetry
poetry install
poetry run codex-ast-mapper --help
```

## Usage

```bash
# Map the current directory (all supported languages)
poetry run codex-ast-mapper --dir . --max-tokens 4000

# Python only, write to a file
poetry run codex-ast-mapper --dir ./my-repo --lang python --max-tokens 2000 -o map.xml -q
```

| Flag | Description |
|------|-------------|
| `--dir` / `-d` | Repository root to walk |
| `--lang` / `-l` | `python` \| `typescript` \| `go` \| `all` |
| `--max-tokens` / `-m` | Hard Tiktoken (`cl100k_base`) budget |
| `--output` / `-o` | Write XML to a path (default: stdout) |
| `--workers` / `-w` | Concurrent parse workers |
| `--quiet` / `-q` | Hide stderr diagnostics |

## Output shape

Hyper-dense minified XML (not Markdown) for maximum LLM recall per token:

```xml
<repo>
  <m id="src.parser">
    <imp src=".models" names="RepoMap,Node"/>
    <c name="RepoParser">
      <init args="root:Path"/>
      <f name="walk" args="ignore:list" ret="Iterator[Node]">
        <doc>Recursively yields non-ignored code files.</doc>
      </f>
    </c>
  </m>
</repo>
```

Inner function bodies are never included. Docstrings are triaged to a summary
line plus `Args:` / `Returns:` blocks. Stdlib / third-party imports are filtered;
relative dependency anchors are kept.

## Progressive pruning

If the map exceeds `--max-tokens`, the pruning matrix steps down:

| Tier | Action | Impact |
|------|--------|--------|
| 1 Mild | Strip private helpers (`_name`) | ~10–15% |
| 2 Moderate | Strip all docstrings | ~20–30% |
| 3 Aggressive | Abbreviate types (`Optional`→`Opt`, …) | up to ~50% |
| Final | Drop import anchors, then low-priority files | fits budget |

## Architecture

```
src/
  cli.py              # Typer interface
  parser.py           # Concurrent walker + Tree-sitter
  ast_extractor.py    # Structural metadata (no bodies)
  encoder.py          # Minified XML + pruning
  tokenizer_util.py   # Tiktoken budget tracker
  models.py           # Shared typed dataclasses
vendor/               # Optional compiled grammar packs
```

## Development

```bash
poetry run ruff check src tests
poetry run ruff format src tests
poetry run mypy src
poetry run pytest -q
```

## License

[GNU General Public License v3.0](LICENSE)
