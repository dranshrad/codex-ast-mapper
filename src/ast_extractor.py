"""Extract structural metadata from Tree-sitter concrete syntax trees."""

from __future__ import annotations

from typing import Any

from src.models import (
    ArgumentMeta,
    ClassMeta,
    FileMeta,
    FunctionMeta,
    Language,
)
from src.parser import ParsedFile

Node = Any


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _child_by_field(node: Node, field: str) -> Node | None:
    return node.child_by_field_name(field)


def _named_children(node: Node) -> list[Node]:
    return list(node.named_children)


def _clean_docstring(raw: str) -> str | None:
    text = raw.strip()
    if len(text) >= 6 and text[:3] in {'"""', "'''"} and text[-3:] == text[:3]:
        text = text[3:-3].strip()
    elif len(text) >= 2 and text[0] in {'"', "'"} and text[-1] == text[0]:
        text = text[1:-1].strip()
    text = " ".join(text.split())
    return text or None


def _is_private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------


def _py_docstring(body: Node | None, source: bytes) -> str | None:
    if body is None or not body.named_children:
        return None
    first = body.named_children[0]
    if first.type == "expression_statement" and first.named_children:
        expr = first.named_children[0]
        if expr.type == "string":
            return _clean_docstring(_text(expr, source))
    return None


def _py_type(node: Node | None, source: bytes) -> str | None:
    if node is None:
        return None
    return _text(node, source).replace(" ", "")


def _py_parameters(params: Node | None, source: bytes) -> list[ArgumentMeta]:
    if params is None:
        return []
    args: list[ArgumentMeta] = []
    for child in params.named_children:
        if child.type in {"identifier"}:
            name = _text(child, source)
            if name not in {"self", "cls"}:
                args.append(ArgumentMeta(name=name))
        elif child.type in {"typed_parameter", "default_parameter", "typed_default_parameter"}:
            name_node = _child_by_field(child, "name") or (
                child.named_children[0] if child.named_children else None
            )
            type_node = _child_by_field(child, "type")
            if name_node is None:
                continue
            name = _text(name_node, source)
            if name in {"self", "cls"}:
                continue
            args.append(ArgumentMeta(name=name, type_hint=_py_type(type_node, source)))
        elif child.type in {"list_splat_pattern", "dictionary_splat_pattern"}:
            # *args / **kwargs
            ident = next((c for c in child.named_children if c.type == "identifier"), None)
            if ident is not None:
                prefix = "*" if child.type == "list_splat_pattern" else "**"
                args.append(ArgumentMeta(name=f"{prefix}{_text(ident, source)}"))
    return args


def _py_decorators(node: Node, source: bytes) -> list[str]:
    decs: list[str] = []
    for child in node.children:
        if child.type == "decorator":
            decs.append(_text(child, source).lstrip("@").split("(")[0].strip())
    return decs


def _py_function(node: Node, source: bytes, *, is_method: bool) -> FunctionMeta:
    name_node = _child_by_field(node, "name")
    name = _text(name_node, source) if name_node else "<anonymous>"
    params = _child_by_field(node, "parameters")
    ret = _child_by_field(node, "return_type")
    body = _child_by_field(node, "body")
    return FunctionMeta(
        name=name,
        args=_py_parameters(params, source),
        return_type=_py_type(ret, source),
        docstring=_py_docstring(body, source),
        is_method=is_method,
        is_private=_is_private(name),
        decorators=_py_decorators(node, source),
    )


def _py_class(node: Node, source: bytes) -> ClassMeta:
    name_node = _child_by_field(node, "name")
    name = _text(name_node, source) if name_node else "<anonymous>"
    bases: list[str] = []
    superclasses = _child_by_field(node, "superclasses")
    if superclasses is not None:
        for child in superclasses.named_children:
            bases.append(_text(child, source).replace(" ", ""))

    body = _child_by_field(node, "body")
    docstring = _py_docstring(body, source)
    methods: list[FunctionMeta] = []
    if body is not None:
        for child in body.named_children:
            if child.type in {"function_definition", "async_function_definition"}:
                methods.append(_py_function(child, source, is_method=True))
            elif child.type == "decorated_definition":
                inner = next(
                    (
                        c
                        for c in child.named_children
                        if c.type in {"function_definition", "async_function_definition"}
                    ),
                    None,
                )
                if inner is not None:
                    fn = _py_function(inner, source, is_method=True)
                    fn.decorators = _py_decorators(child, source) + fn.decorators
                    methods.append(fn)

    return ClassMeta(
        name=name,
        bases=bases,
        docstring=docstring,
        methods=methods,
        is_private=_is_private(name),
    )


def _extract_python(
    root: Node, source: bytes
) -> tuple[list[str], list[ClassMeta], list[FunctionMeta]]:
    imports: list[str] = []
    classes: list[ClassMeta] = []
    functions: list[FunctionMeta] = []

    def walk(node: Node) -> None:
        if node.type == "import_statement":
            imports.append(_text(node, source).removeprefix("import ").replace(" ", ""))
        elif node.type == "import_from_statement":
            module = _child_by_field(node, "module_name")
            mod = _text(module, source) if module else ""
            names: list[str] = []
            for child in node.named_children:
                if (
                    child.type in {"dotted_name", "aliased_import", "identifier"}
                    and child != module
                ):
                    names.append(_text(child, source).split(" as ")[0].strip())
            if names:
                imports.append(f"{mod}:{','.join(names)}" if mod else ",".join(names))
            elif mod:
                imports.append(mod)
        elif node.type == "class_definition":
            classes.append(_py_class(node, source))
            return
        elif node.type in {"function_definition", "async_function_definition"}:
            functions.append(_py_function(node, source, is_method=False))
            return
        elif node.type == "decorated_definition":
            inner = next(
                (
                    c
                    for c in node.named_children
                    if c.type
                    in {
                        "class_definition",
                        "function_definition",
                        "async_function_definition",
                    }
                ),
                None,
            )
            if inner is None:
                return
            if inner.type == "class_definition":
                classes.append(_py_class(inner, source))
            else:
                fn = _py_function(inner, source, is_method=False)
                fn.decorators = _py_decorators(node, source) + fn.decorators
                functions.append(fn)
            return

        for child in node.named_children:
            walk(child)

    walk(root)
    return imports, classes, functions


# ---------------------------------------------------------------------------
# TypeScript / TSX
# ---------------------------------------------------------------------------


def _ts_type(node: Node | None, source: bytes) -> str | None:
    if node is None:
        return None
    return _text(node, source).replace(" ", "").removeprefix(":").strip() or None


def _ts_params(params: Node | None, source: bytes) -> list[ArgumentMeta]:
    if params is None:
        return []
    args: list[ArgumentMeta] = []
    for child in params.named_children:
        if child.type not in {
            "required_parameter",
            "optional_parameter",
            "rest_parameter",
        }:
            continue
        pattern = _child_by_field(child, "pattern")
        type_node = _child_by_field(child, "type")
        if pattern is None:
            # fall back to first identifier-ish child
            pattern = next(
                (c for c in child.named_children if c.type in {"identifier", "object_pattern"}),
                None,
            )
        if pattern is None:
            continue
        name = _text(pattern, source)
        if child.type == "rest_parameter" and not name.startswith("..."):
            name = f"...{name}"
        args.append(ArgumentMeta(name=name, type_hint=_ts_type(type_node, source)))
    return args


def _ts_function_like(node: Node, source: bytes, *, is_method: bool) -> FunctionMeta | None:
    name_node = _child_by_field(node, "name")
    if name_node is None and node.type == "function_declaration":
        return None
    name = _text(name_node, source) if name_node else "<anonymous>"
    params = _child_by_field(node, "parameters")
    ret = _child_by_field(node, "return_type")
    return FunctionMeta(
        name=name,
        args=_ts_params(params, source),
        return_type=_ts_type(ret, source),
        docstring=None,
        is_method=is_method,
        is_private=_is_private(name) or name.startswith("#"),
    )


def _ts_class(node: Node, source: bytes) -> ClassMeta:
    name_node = _child_by_field(node, "name")
    name = _text(name_node, source) if name_node else "<anonymous>"
    bases: list[str] = []
    heritage = next((c for c in node.named_children if c.type == "class_heritage"), None)
    if heritage is not None:
        for child in heritage.named_children:
            if child.type in {"extends_clause", "implements_clause"}:
                for value in child.named_children:
                    bases.append(_text(value, source).replace(" ", ""))

    body = _child_by_field(node, "body")
    methods: list[FunctionMeta] = []
    if body is not None:
        for child in body.named_children:
            if child.type in {
                "method_definition",
                "public_field_definition",
                "abstract_method_signature",
            }:
                # Only treat callable members as methods
                if child.type == "public_field_definition":
                    value = _child_by_field(child, "value")
                    if value is None or value.type not in {
                        "arrow_function",
                        "function_expression",
                    }:
                        continue
                    name_n = _child_by_field(child, "name")
                    if name_n is None:
                        continue
                    fn_name = _text(name_n, source)
                    methods.append(
                        FunctionMeta(
                            name=fn_name,
                            args=_ts_params(_child_by_field(value, "parameters"), source),
                            return_type=_ts_type(_child_by_field(value, "return_type"), source),
                            is_method=True,
                            is_private=_is_private(fn_name) or fn_name.startswith("#"),
                        )
                    )
                else:
                    fn = _ts_function_like(child, source, is_method=True)
                    if fn is not None:
                        methods.append(fn)

    return ClassMeta(name=name, bases=bases, methods=methods, is_private=_is_private(name))


def _extract_typescript(
    root: Node, source: bytes
) -> tuple[list[str], list[ClassMeta], list[FunctionMeta]]:
    imports: list[str] = []
    classes: list[ClassMeta] = []
    functions: list[FunctionMeta] = []

    def walk(node: Node) -> None:
        if node.type == "import_statement":
            source_node = _child_by_field(node, "source")
            if source_node is not None:
                imports.append(_text(source_node, source).strip("'\""))
            else:
                imports.append(_text(node, source).replace(" ", "")[:80])
            return
        if node.type == "class_declaration":
            classes.append(_ts_class(node, source))
            return
        if node.type == "function_declaration":
            fn = _ts_function_like(node, source, is_method=False)
            if fn is not None:
                functions.append(fn)
            return
        if node.type == "lexical_declaration":
            for declarator in (c for c in node.named_children if c.type == "variable_declarator"):
                name_n = _child_by_field(declarator, "name")
                value = _child_by_field(declarator, "value")
                if (
                    name_n is not None
                    and value is not None
                    and value.type in {"arrow_function", "function_expression"}
                ):
                    fn_name = _text(name_n, source)
                    functions.append(
                        FunctionMeta(
                            name=fn_name,
                            args=_ts_params(_child_by_field(value, "parameters"), source),
                            return_type=_ts_type(_child_by_field(value, "return_type"), source),
                            is_private=_is_private(fn_name),
                        )
                    )
            return
        if node.type == "export_statement":
            for child in node.named_children:
                walk(child)
            return

        for child in node.named_children:
            walk(child)

    walk(root)
    return imports, classes, functions


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------


def _go_type(node: Node | None, source: bytes) -> str | None:
    if node is None:
        return None
    return _text(node, source).replace(" ", "")


def _go_params(params: Node | None, source: bytes) -> list[ArgumentMeta]:
    if params is None:
        return []
    args: list[ArgumentMeta] = []
    for child in params.named_children:
        if child.type != "parameter_declaration":
            continue
        type_node = _child_by_field(child, "type")
        type_hint = _go_type(type_node, source)
        names = [c for c in child.named_children if c.type == "identifier" and c != type_node]
        if not names:
            args.append(ArgumentMeta(name="_", type_hint=type_hint))
        else:
            for name_n in names:
                args.append(ArgumentMeta(name=_text(name_n, source), type_hint=type_hint))
    return args


def _go_result_type(result: Node | None, source: bytes) -> str | None:
    if result is None:
        return None
    if result.type == "parameter_list":
        parts = [_go_type(c, source) or "" for c in result.named_children]
        joined = ",".join(p for p in parts if p)
        return f"({joined})" if joined else None
    return _go_type(result, source)


def _go_function(node: Node, source: bytes) -> FunctionMeta:
    name_node = _child_by_field(node, "name")
    name = _text(name_node, source) if name_node else "<anonymous>"
    params = _child_by_field(node, "parameters")
    result = _child_by_field(node, "result")
    return FunctionMeta(
        name=name,
        args=_go_params(params, source),
        return_type=_go_result_type(result, source),
        is_method=False,
        is_private=name[:1].islower() if name else False,
    )


def _go_method(node: Node, source: bytes) -> tuple[str | None, FunctionMeta]:
    """Return (receiver type, method meta)."""
    receiver = _child_by_field(node, "receiver")
    recv_type: str | None = None
    if receiver is not None and receiver.named_children:
        param = receiver.named_children[0]
        type_node = _child_by_field(param, "type")
        recv_type = _go_type(type_node, source)
        if recv_type:
            recv_type = recv_type.lstrip("*")

    name_node = _child_by_field(node, "name")
    name = _text(name_node, source) if name_node else "<anonymous>"
    fn = FunctionMeta(
        name=name,
        args=_go_params(_child_by_field(node, "parameters"), source),
        return_type=_go_result_type(_child_by_field(node, "result"), source),
        is_method=True,
        is_private=name[:1].islower() if name else False,
    )
    return recv_type, fn


def _go_type_spec(node: Node, source: bytes) -> ClassMeta | None:
    name_node = _child_by_field(node, "name")
    type_node = _child_by_field(node, "type")
    if name_node is None or type_node is None:
        return None
    name = _text(name_node, source)
    bases: list[str] = []
    if type_node.type == "struct_type":
        # No inheritance; embed fields as bases for density
        field_list = next(
            (c for c in type_node.named_children if c.type == "field_declaration_list"), None
        )
        if field_list is not None:
            for field in field_list.named_children:
                if field.type != "field_declaration":
                    continue
                # Embedded field: type only, no name
                names = [c for c in field.named_children if c.type == "field_identifier"]
                type_n = _child_by_field(field, "type")
                if not names and type_n is not None:
                    bases.append(_go_type(type_n, source) or "")
    elif type_node.type == "interface_type":
        pass
    else:
        return None

    return ClassMeta(
        name=name,
        bases=[b for b in bases if b],
        methods=[],
        is_private=name[:1].islower(),
    )


def _extract_go(root: Node, source: bytes) -> tuple[list[str], list[ClassMeta], list[FunctionMeta]]:
    imports: list[str] = []
    classes: list[ClassMeta] = []
    functions: list[FunctionMeta] = []
    methods_by_recv: dict[str, list[FunctionMeta]] = {}

    def walk(node: Node) -> None:
        if node.type == "import_declaration":
            for child in node.named_children:
                if child.type == "import_spec":
                    path_n = _child_by_field(child, "path")
                    if path_n is not None:
                        imports.append(_text(path_n, source).strip('"'))
                elif child.type == "import_spec_list":
                    for spec in child.named_children:
                        if spec.type == "import_spec":
                            path_n = _child_by_field(spec, "path")
                            if path_n is not None:
                                imports.append(_text(path_n, source).strip('"'))
            return
        if node.type == "type_declaration":
            for child in node.named_children:
                if child.type == "type_spec":
                    cls = _go_type_spec(child, source)
                    if cls is not None:
                        classes.append(cls)
            return
        if node.type == "function_declaration":
            functions.append(_go_function(node, source))
            return
        if node.type == "method_declaration":
            recv, fn = _go_method(node, source)
            if recv:
                methods_by_recv.setdefault(recv, []).append(fn)
            else:
                functions.append(fn)
            return

        for child in node.named_children:
            walk(child)

    walk(root)

    class_names = {c.name: c for c in classes}
    for recv, methods in methods_by_recv.items():
        if recv in class_names:
            class_names[recv].methods.extend(methods)
        else:
            classes.append(ClassMeta(name=recv, methods=methods, is_private=recv[:1].islower()))

    return imports, classes, functions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_file(parsed: ParsedFile) -> FileMeta:
    """Extract structural metadata from a parsed source file (bodies stripped)."""
    root = parsed.root_node
    source = parsed.source

    if parsed.language is Language.PYTHON:
        imports, classes, functions = _extract_python(root, source)
    elif parsed.language is Language.TYPESCRIPT:
        imports, classes, functions = _extract_typescript(root, source)
    elif parsed.language is Language.GO:
        imports, classes, functions = _extract_go(root, source)
    else:
        imports, classes, functions = [], [], []

    return FileMeta(
        path=parsed.relative_path,
        language=parsed.language,
        imports=imports,
        classes=classes,
        functions=functions,
    )


def extract_all(parsed_files: list[ParsedFile]) -> list[FileMeta]:
    """Extract metadata for every parsed file."""
    return [extract_file(parsed) for parsed in parsed_files]
