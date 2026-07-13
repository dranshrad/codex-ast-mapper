"""Encode structural metadata into minified LLM-dense XML with token pruning."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from src.models import (
    PRUNE_ORDER,
    ArgumentMeta,
    ClassMeta,
    FileMeta,
    FunctionMeta,
    PruneLevel,
)
from src.tokenizer_util import TokenBudget


def _esc(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _args_attr(args: list[ArgumentMeta], *, include_types: bool) -> str:
    if not args:
        return ""
    parts: list[str] = []
    for arg in args:
        if include_types and arg.type_hint:
            parts.append(f"{arg.name}:{arg.type_hint}")
        else:
            parts.append(arg.name)
    return f' args="{_esc(",".join(parts))}"'


def _fn_xml(fn: FunctionMeta, *, include_doc: bool, include_types: bool, tag: str) -> str:
    ret = f' ret="{_esc(fn.return_type)}"' if include_types and fn.return_type else ""
    dec = f' dec="{_esc(",".join(fn.decorators))}"' if fn.decorators else ""
    body = f"<doc>{_esc(fn.docstring)}</doc>" if include_doc and fn.docstring else ""
    open_tag = (
        f'<{tag} name="{_esc(fn.name)}"{_args_attr(fn.args, include_types=include_types)}{ret}{dec}'
    )
    if body:
        return f"{open_tag}>{body}</{tag}>"
    return f"{open_tag}/>"


def _class_xml(
    cls: ClassMeta, *, include_doc: bool, include_types: bool, include_helpers: bool
) -> str:
    bases = f' bases="{_esc(",".join(cls.bases))}"' if cls.bases else ""
    methods = cls.methods if include_helpers else [m for m in cls.methods if not m.is_private]
    method_xml = "".join(
        _fn_xml(m, include_doc=include_doc, include_types=include_types, tag="method")
        for m in methods
    )
    doc = f"<doc>{_esc(cls.docstring)}</doc>" if include_doc and cls.docstring else ""
    return f'<class name="{_esc(cls.name)}"{bases}>{doc}{method_xml}</class>'


def _file_xml(
    file_meta: FileMeta,
    *,
    include_doc: bool,
    include_types: bool,
    include_helpers: bool,
    include_imports: bool,
) -> str:
    classes = (
        file_meta.classes if include_helpers else [c for c in file_meta.classes if not c.is_private]
    )
    functions = (
        file_meta.functions
        if include_helpers
        else [f for f in file_meta.functions if not f.is_private]
    )
    imp = ""
    if include_imports and file_meta.imports:
        imp = f"<imp>{_esc(','.join(file_meta.imports))}</imp>"
    class_xml = "".join(
        _class_xml(
            c,
            include_doc=include_doc,
            include_types=include_types,
            include_helpers=include_helpers,
        )
        for c in classes
    )
    fn_xml = "".join(
        _fn_xml(f, include_doc=include_doc, include_types=include_types, tag="fn")
        for f in functions
    )
    return (
        f'<file path="{_esc(file_meta.path)}" lang="{file_meta.language.short}">'
        f"{imp}{class_xml}{fn_xml}</file>"
    )


def encode_map(
    files: list[FileMeta],
    *,
    include_doc: bool = True,
    include_types: bool = True,
    include_helpers: bool = True,
    include_imports: bool = True,
) -> str:
    """Serialize file metadata into a single minified XML document."""
    body = "".join(
        _file_xml(
            f,
            include_doc=include_doc,
            include_types=include_types,
            include_helpers=include_helpers,
            include_imports=include_imports,
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


def _flags_for(level: PruneLevel) -> dict[str, bool]:
    include_doc = True
    include_helpers = True
    include_types = True
    include_imports = True

    if level in {"docstrings", "helpers", "types", "imports"}:
        include_doc = False
    if level in {"helpers", "types", "imports"}:
        include_helpers = False
    if level in {"types", "imports"}:
        include_types = False
    if level == "imports":
        include_imports = False

    return {
        "include_doc": include_doc,
        "include_types": include_types,
        "include_helpers": include_helpers,
        "include_imports": include_imports,
    }


def _drop_lowest_priority_file(files: list[FileMeta]) -> list[FileMeta]:
    """Drop the file with the fewest public symbols (prefer keeping dense API surfaces)."""
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


def encode_within_budget(
    files: list[FileMeta],
    budget: TokenBudget,
) -> EncodeResult:
    """
    Encode metadata into XML, progressively pruning to stay under ``budget``.

    Strategy (in order):
      1. Full map (docstrings + helpers + types + imports)
      2. Strip docstrings
      3. Strip private/helper methods and private classes/functions
      4. Strip type annotations
      5. Strip import graphs
      6. Drop lowest-priority files until the map fits (or a single file remains)
    """
    working = deepcopy(files)
    levels: tuple[PruneLevel, ...] = ("none", *PRUNE_ORDER)

    for level in levels:
        flags = _flags_for(level)
        xml = encode_map(working, **flags)
        tokens = budget.measure(xml)
        if tokens <= budget.max_tokens:
            return EncodeResult(
                xml=xml,
                tokens=tokens,
                prune_level=level,
                within_budget=True,
            )

    # Still over budget: drop files greedily while keeping max prune applied.
    flags = _flags_for("imports")
    while len(working) > 1:
        working = _drop_lowest_priority_file(working)
        xml = encode_map(working, **flags)
        tokens = budget.measure(xml)
        if tokens <= budget.max_tokens:
            return EncodeResult(
                xml=xml,
                tokens=tokens,
                prune_level="imports",
                within_budget=True,
            )

    xml = encode_map(working, **flags)
    tokens = budget.measure(xml)
    return EncodeResult(
        xml=xml,
        tokens=tokens,
        prune_level="imports",
        within_budget=tokens <= budget.max_tokens,
    )
