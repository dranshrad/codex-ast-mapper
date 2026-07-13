"""Hyper-dense minified XML encoding with progressive token-budget pruning."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from src.models import (
    PRUNE_ORDER,
    ArgumentMeta,
    ClassMeta,
    FileMeta,
    FunctionMeta,
    ImportMeta,
    PruneLevel,
)
from src.tokenizer_util import TokenBudget

# Tier-3 type abbreviations for aggressive compression.
_TYPE_ABBREV: dict[str, str] = {
    "Optional": "Opt",
    "Union": "U",
    "List": "L",
    "Dict": "D",
    "Tuple": "T",
    "Callable": "Fn",
    "Iterator": "Iter",
    "AsyncIterator": "AIter",
    "Awaitable": "Aw",
    "Sequence": "Seq",
    "Mapping": "Map",
    "Iterable": "It",
    "Generator": "Gen",
    "Coroutine": "Co",
    "TypeVar": "TV",
    "None": "-",
    "string": "s",
    "number": "n",
    "boolean": "b",
    "object": "o",
}


def _esc(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _abbrev_type(hint: str | None, *, compress: bool) -> str | None:
    if hint is None:
        return None
    if not compress:
        return hint
    out = hint
    # Longest keys first to avoid partial collisions
    for full, short in sorted(_TYPE_ABBREV.items(), key=lambda kv: len(kv[0]), reverse=True):
        out = out.replace(full, short)
    # Collapse nested whitespace already removed; trim very long generics
    if len(out) > 48:
        out = out[:45] + "…"
    return out


def _args_attr(
    args: list[ArgumentMeta],
    *,
    include_types: bool,
    compress_types: bool,
) -> str:
    if not args:
        return ""
    parts: list[str] = []
    for arg in args:
        if include_types and arg.type_hint:
            hint = _abbrev_type(arg.type_hint, compress=compress_types)
            parts.append(f"{arg.name}:{hint}")
        else:
            parts.append(arg.name)
    return f' args="{_esc(",".join(parts))}"'


def _imp_xml(imp: ImportMeta) -> str:
    names = f' names="{_esc(",".join(imp.names))}"' if imp.names else ""
    return f'<imp src="{_esc(imp.source)}"{names}/>'


def _fn_xml(
    fn: FunctionMeta,
    *,
    include_doc: bool,
    include_types: bool,
    include_helpers: bool,
    compress_types: bool,
) -> str:
    if not include_helpers and fn.is_private:
        return ""

    ret_raw = _abbrev_type(fn.return_type, compress=compress_types) if include_types else None
    ret = f' ret="{_esc(ret_raw)}"' if ret_raw else ""
    async_attr = ' a="1"' if fn.is_async else ""
    gen_attr = ' g="1"' if fn.is_generator else ""
    dec = f' dec="{_esc(",".join(fn.decorators))}"' if fn.decorators else ""
    nested = "".join(
        _fn_xml(
            child,
            include_doc=include_doc,
            include_types=include_types,
            include_helpers=include_helpers,
            compress_types=compress_types,
        )
        for child in fn.nested
    )
    doc = f"<doc>{_esc(fn.docstring)}</doc>" if include_doc and fn.docstring else ""

    # __init__ → <init …/>
    tag = "init" if fn.name == "__init__" else "f"
    name_attr = "" if tag == "init" else f' name="{_esc(fn.name)}"'
    open_tag = (
        f"<{tag}{name_attr}"
        f"{_args_attr(fn.args, include_types=include_types, compress_types=compress_types)}"
        f"{ret}{async_attr}{gen_attr}{dec}"
    )
    inner = f"{doc}{nested}"
    if inner:
        return f"{open_tag}>{inner}</{tag}>"
    return f"{open_tag}/>"


def _class_xml(
    cls: ClassMeta,
    *,
    include_doc: bool,
    include_types: bool,
    include_helpers: bool,
    compress_types: bool,
) -> str:
    if not include_helpers and cls.is_private:
        return ""

    bases = f' bases="{_esc(",".join(cls.bases))}"' if cls.bases else ""
    methods = "".join(
        _fn_xml(
            m,
            include_doc=include_doc,
            include_types=include_types,
            include_helpers=include_helpers,
            compress_types=compress_types,
        )
        for m in cls.methods
    )
    nested = "".join(
        _class_xml(
            child,
            include_doc=include_doc,
            include_types=include_types,
            include_helpers=include_helpers,
            compress_types=compress_types,
        )
        for child in cls.nested_classes
    )
    doc = f"<doc>{_esc(cls.docstring)}</doc>" if include_doc and cls.docstring else ""
    return f'<c name="{_esc(cls.name)}"{bases}>{doc}{methods}{nested}</c>'


def _module_xml(
    file_meta: FileMeta,
    *,
    include_doc: bool,
    include_types: bool,
    include_helpers: bool,
    include_imports: bool,
    compress_types: bool,
) -> str:
    mid = file_meta.module_id or file_meta.path
    imports = ""
    if include_imports:
        imports = "".join(_imp_xml(i) for i in file_meta.imports)

    classes = "".join(
        _class_xml(
            c,
            include_doc=include_doc,
            include_types=include_types,
            include_helpers=include_helpers,
            compress_types=compress_types,
        )
        for c in file_meta.classes
    )
    functions = "".join(
        _fn_xml(
            f,
            include_doc=include_doc,
            include_types=include_types,
            include_helpers=include_helpers,
            compress_types=compress_types,
        )
        for f in file_meta.functions
    )
    return f'<m id="{_esc(mid)}">{imports}{classes}{functions}</m>'


@dataclass(frozen=True, slots=True)
class EncodeFlags:
    include_doc: bool = True
    include_types: bool = True
    include_helpers: bool = True
    include_imports: bool = True
    compress_types: bool = False


def encode_map(files: list[FileMeta], flags: EncodeFlags | None = None, **legacy: bool) -> str:
    """
    Serialize file metadata into hyper-dense minified XML.

    Example::

        <repo><m id="src.parser"><imp src=".models" names="RepoMap,Node"/>
        <c name="RepoParser"><init args="root:Path"/>
        <f name="walk" args="ignore:list" ret="Iter[Node]">
        <doc>Recursively yields non-ignored code files.</doc></f></c></m></repo>
    """
    if flags is None:
        flags = EncodeFlags(
            include_doc=legacy.get("include_doc", True),
            include_types=legacy.get("include_types", True),
            include_helpers=legacy.get("include_helpers", True),
            include_imports=legacy.get("include_imports", True),
            compress_types=legacy.get("compress_types", False),
        )
    body = "".join(
        _module_xml(
            f,
            include_doc=flags.include_doc,
            include_types=flags.include_types,
            include_helpers=flags.include_helpers,
            include_imports=flags.include_imports,
            compress_types=flags.compress_types,
        )
        for f in files
    )
    return f"<repo>{body}</repo>"


@dataclass(frozen=True, slots=True)
class EncodeResult:
    xml: str
    tokens: int
    prune_level: PruneLevel
    within_budget: bool


def _flags_for(level: PruneLevel) -> EncodeFlags:
    """
    Progressive pruning matrix:

    Tier 1 (helpers)     - strip private helpers (~10-15%)
    Tier 2 (docstrings)  - strip all docs (~20-30%)
    Tier 3 (types)       - abbreviate types (~50% cumulative)
    imports              - drop import anchors, then files
    """
    if level == "none":
        return EncodeFlags()
    if level == "helpers":
        return EncodeFlags(include_helpers=False)
    if level == "docstrings":
        return EncodeFlags(include_helpers=False, include_doc=False)
    if level == "types":
        return EncodeFlags(
            include_helpers=False,
            include_doc=False,
            compress_types=True,
        )
    # imports
    return EncodeFlags(
        include_helpers=False,
        include_doc=False,
        compress_types=True,
        include_imports=False,
    )


def _drop_lowest_priority_file(files: list[FileMeta]) -> list[FileMeta]:
    """Drop the file with the fewest public symbols."""
    if len(files) <= 1:
        return files

    def score(file_meta: FileMeta) -> tuple[int, int, str]:
        public_methods = sum(1 for c in file_meta.classes for m in c.methods if not m.is_private)
        public_fns = sum(1 for f in file_meta.functions if not f.is_private)
        public_classes = sum(1 for c in file_meta.classes if not c.is_private)
        return (
            public_classes + public_methods + public_fns,
            len(file_meta.imports),
            file_meta.path,
        )

    ranked = sorted(files, key=score)
    drop_path = ranked[0].path
    return [f for f in files if f.path != drop_path]


def encode_within_budget(files: list[FileMeta], budget: TokenBudget) -> EncodeResult:
    """Encode to XML, stepping through the pruning matrix until under budget."""
    working = deepcopy(files)
    levels: tuple[PruneLevel, ...] = ("none", *PRUNE_ORDER)

    for level in levels:
        flags = _flags_for(level)
        xml = encode_map(working, flags)
        tokens = budget.measure(xml)
        if tokens <= budget.max_tokens:
            return EncodeResult(
                xml=xml,
                tokens=tokens,
                prune_level=level,
                within_budget=True,
            )

    flags = _flags_for("imports")
    while len(working) > 1:
        working = _drop_lowest_priority_file(working)
        xml = encode_map(working, flags)
        tokens = budget.measure(xml)
        if tokens <= budget.max_tokens:
            return EncodeResult(
                xml=xml,
                tokens=tokens,
                prune_level="imports",
                within_budget=True,
            )

    xml = encode_map(working, flags)
    tokens = budget.measure(xml)
    return EncodeResult(
        xml=xml,
        tokens=tokens,
        prune_level="imports",
        within_budget=tokens <= budget.max_tokens,
    )
