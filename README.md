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

```xml
<repo>
  <file path="src/user.py" lang="py">
    <imp>typing,os.path</imp>
    <class name="User" bases="BaseModel">
      <doc>Account owner.</doc>
      <method name="sync" args="id:int" ret="None"/>
    </class>
    <fn name="load" args="path:str" ret="User"/>
  </file>
</repo>
```

Inner function bodies are never included — only signatures and structure.

## Progressive pruning

If the map exceeds `--max-tokens`, pruning runs in order:

1. Strip docstrings  
2. Drop private / helper methods (`_name`)  
3. Strip type annotations  
4. Strip import graphs  
5. Drop lowest-priority files until the budget fits  

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
