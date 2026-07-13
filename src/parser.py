"""Concurrent directory walker and Tree-sitter parser initialization."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import pathspec
import tree_sitter_go as ts_go
import tree_sitter_python as ts_python
import tree_sitter_typescript as ts_typescript
from tree_sitter import Language, Parser

from src.models import SUPPORTED_EXTENSIONS
from src.models import Language as SourceLanguage

# Compiled grammar handles live in-process; vendor/ is reserved for optional
# on-disk language packs / future custom grammars.
VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"

_LANGUAGE_OBJECTS: dict[SourceLanguage, Language] = {
    SourceLanguage.PYTHON: Language(ts_python.language()),
    SourceLanguage.TYPESCRIPT: Language(ts_typescript.language_typescript()),
    SourceLanguage.GO: Language(ts_go.language()),
}


@dataclass(frozen=True, slots=True)
class ParsedFile:
    """A source file with its concrete syntax tree root."""

    path: Path
    relative_path: str
    language: SourceLanguage
    source: bytes
    root_node: object  # tree_sitter.Node — kept as object to ease typing across versions


def _load_gitignore(root: Path) -> pathspec.PathSpec:
    patterns: list[str] = [
        ".git/",
        "__pycache__/",
        "node_modules/",
        "vendor/",
        ".venv/",
        "dist/",
        "build/",
        "*.egg-info/",
    ]
    gitignore = root / ".gitignore"
    if gitignore.is_file():
        patterns.extend(
            line
            for line in gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def discover_source_files(
    root: Path,
    languages: set[SourceLanguage] | None = None,
) -> list[Path]:
    """Walk ``root`` concurrently-friendly, honoring ``.gitignore`` patterns."""
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")

    spec = _load_gitignore(root)
    allowed = languages or set(SourceLanguage)
    found: list[Path] = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if spec.match_file(rel):
            continue
        lang = SUPPORTED_EXTENSIONS.get(path.suffix.lower())
        if lang is None or lang not in allowed:
            continue
        found.append(path)

    return sorted(found)


def _parser_for(language: SourceLanguage) -> Parser:
    parser = Parser(_LANGUAGE_OBJECTS[language])
    return parser


def parse_file(path: Path, root: Path) -> ParsedFile | None:
    """Parse a single source file into a Tree-sitter CST."""
    language = SUPPORTED_EXTENSIONS.get(path.suffix.lower())
    if language is None:
        return None

    try:
        source = path.read_bytes()
    except OSError:
        return None

    # Skip extremely large blobs to keep memory bounded.
    if len(source) > 2_000_000:
        return None

    tree = _parser_for(language).parse(source)
    return ParsedFile(
        path=path,
        relative_path=path.relative_to(root).as_posix(),
        language=language,
        source=source,
        root_node=tree.root_node,
    )


def walk_and_parse(
    root: Path,
    languages: set[SourceLanguage] | None = None,
    *,
    max_workers: int | None = None,
    on_error: Callable[[Path, Exception], None] | None = None,
) -> list[ParsedFile]:
    """Discover and parse source files using a thread pool."""
    root = root.resolve()
    files = discover_source_files(root, languages)
    if not files:
        return []

    workers = max_workers or min(32, max(4, (len(files) // 8) + 1))
    results: list[ParsedFile] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(parse_file, path, root): path for path in files}
        for future in as_completed(futures):
            path = futures[future]
            try:
                parsed = future.result()
            except Exception as exc:
                if on_error is not None:
                    on_error(path, exc)
                continue
            if parsed is not None:
                results.append(parsed)

    results.sort(key=lambda item: item.relative_path)
    return results


def ensure_vendor_dir() -> Path:
    """Ensure the vendor directory exists for optional compiled grammars."""
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    return VENDOR_DIR
