"""Shared typed models for extracted AST metadata and repository graphs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class Language(str, Enum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    GO = "go"

    @property
    def short(self) -> str:
        mapping: dict[Language, str] = {
            Language.PYTHON: "py",
            Language.TYPESCRIPT: "ts",
            Language.GO: "go",
        }
        return mapping[self]


SUPPORTED_EXTENSIONS: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".pyi": Language.PYTHON,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".go": Language.GO,
}


@dataclass(slots=True)
class ArgumentMeta:
    name: str
    type_hint: str | None = None


@dataclass(slots=True)
class ImportMeta:
    """Internal dependency-graph anchor (relative / project imports only)."""

    source: str
    names: list[str] = field(default_factory=list)
    is_relative: bool = False
    resolved: str | None = None  # filled by graph builder when known


@dataclass(slots=True)
class FunctionMeta:
    name: str
    args: list[ArgumentMeta] = field(default_factory=list)
    return_type: str | None = None
    docstring: str | None = None
    is_method: bool = False
    is_private: bool = False
    is_async: bool = False
    is_generator: bool = False
    decorators: list[str] = field(default_factory=list)
    nested: list[FunctionMeta] = field(default_factory=list)
    scope: str = ""  # dotted parent path, e.g. "module.Class"


@dataclass(slots=True)
class ClassMeta:
    name: str
    bases: list[str] = field(default_factory=list)
    docstring: str | None = None
    methods: list[FunctionMeta] = field(default_factory=list)
    nested_classes: list[ClassMeta] = field(default_factory=list)
    is_private: bool = False
    scope: str = ""


@dataclass(slots=True)
class FileMeta:
    path: str
    language: Language
    module_id: str = ""
    imports: list[ImportMeta] = field(default_factory=list)
    classes: list[ClassMeta] = field(default_factory=list)
    functions: list[FunctionMeta] = field(default_factory=list)
    importance: float = 0.0  # filled by graph builder


class EdgeKind(str, Enum):
    IMPORTS = "imports"
    INHERITS = "inherits"
    CONTAINS = "contains"
    DEFINES = "defines"


@dataclass(slots=True)
class SymbolRef:
    """Stable symbol identity, e.g. ``pkg.user:User.sync``."""

    id: str
    name: str
    kind: str  # module | class | function | method
    module_id: str
    is_private: bool = False


@dataclass(slots=True)
class GraphEdge:
    source: str
    target: str
    kind: EdgeKind
    name: str | None = None


@dataclass(slots=True)
class RepoGraph:
    modules: dict[str, FileMeta] = field(default_factory=dict)
    symbols: dict[str, SymbolRef] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    importance: dict[str, float] = field(default_factory=dict)  # module_id -> score


# Tier 1 = helpers, Tier 2 = docstrings, Tier 3 = type compression, then imports/files
PruneLevel = Literal["none", "helpers", "docstrings", "types", "imports"]


PRUNE_ORDER: tuple[PruneLevel, ...] = (
    "helpers",
    "docstrings",
    "types",
    "imports",
)


LLMMode = Literal["developer", "review", "planning", "docs", "refactor"]


OutputFormat = Literal["xml", "json", "mermaid"]
