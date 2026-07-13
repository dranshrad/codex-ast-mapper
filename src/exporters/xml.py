"""Hyper-dense minified XML exporter."""

from __future__ import annotations

from dataclasses import dataclass

from src.models import (
    ArgumentMeta,
    ClassMeta,
    FileMeta,
    FunctionMeta,
    ImportMeta,
)

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
    for full, short in sorted(_TYPE_ABBREV.items(), key=lambda kv: len(kv[0]), reverse=True):
        out = out.replace(full, short)
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
    src = imp.resolved or imp.source
    return f'<imp src="{_esc(src)}"{names}/>'


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


def export_xml(files: list[FileMeta], flags: EncodeFlags | None = None, **legacy: bool) -> str:
    """Serialize file metadata into hyper-dense minified XML."""
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


# Back-compat alias used by older call sites / tests
encode_map = export_xml
