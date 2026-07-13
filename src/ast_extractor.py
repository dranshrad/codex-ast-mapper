"""Extract structural metadata from Tree-sitter CSTs via scoped node visitors."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, ClassVar

from src.models import (
    ArgumentMeta,
    ClassMeta,
    FileMeta,
    FunctionMeta,
    ImportMeta,
    Language,
)
from src.parser import ParsedFile

Node = Any

# Stdlib + ubiquitous third-party roots filtered from the dependency graph.
_STDLIB_ROOTS: frozenset[str] = frozenset(sys.stdlib_module_names) | frozenset(
    {
        "typing_extensions",
        "pkg_resources",
        "setuptools",
        "pip",
        "wheel",
        "numpy",
        "pandas",
        "requests",
        "urllib3",
        "httpx",
        "aiohttp",
        "flask",
        "django",
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "pytest",
        "mypy",
        "ruff",
        "black",
        "click",
        "typer",
        "rich",
        "tiktoken",
        "pathspec",
        "tree_sitter",
        "tree_sitter_python",
        "tree_sitter_typescript",
        "tree_sitter_go",
    }
)


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _child_by_field(node: Node, field_name: str) -> Node | None:
    return node.child_by_field_name(field_name)


def _is_private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _module_id_from_path(relative_path: str) -> str:
    """Convert ``src/parser.py`` → ``src.parser``."""
    path = relative_path.replace("\\", "/")
    if path.endswith(".py"):
        path = path[: -len(".py")]
    elif path.endswith(".pyi"):
        path = path[: -len(".pyi")]
    elif path.endswith(".tsx"):
        path = path[: -len(".tsx")]
    elif path.endswith(".ts"):
        path = path[: -len(".ts")]
    elif path.endswith(".go"):
        path = path[: -len(".go")]
    parts = [p for p in path.split("/") if p and p != "__init__"]
    return ".".join(parts)


def triage_docstring(raw: str) -> str | None:
    """
    Keep only structural intent: first summary line, plus Args:/Returns: blocks.

    Conversational paragraphs after the summary are discarded to protect token budget.
    """
    text = raw.strip()
    if len(text) >= 6 and text[:3] in {'"""', "'''"} and text[-3:] == text[:3]:
        text = text[3:-3].strip()
    elif len(text) >= 2 and text[0] in {'"', "'"} and text[-1] == text[0]:
        text = text[1:-1].strip()
    if not text:
        return None

    lines = [ln.rstrip() for ln in text.splitlines()]
    summary = ""
    for ln in lines:
        stripped = ln.strip()
        if stripped:
            summary = stripped
            break
    if not summary:
        return None

    structural_headers = {
        "Args:",
        "Arguments:",
        "Parameters:",
        "Returns:",
        "Return:",
        "Yields:",
        "Raises:",
        "Examples:",
        "Note:",
        "Notes:",
    }
    kept: list[str] = [summary]
    header_aliases = {h.rstrip(":") for h in structural_headers}
    capturing: str | None = None
    for ln in lines:
        stripped = ln.strip()
        if stripped in structural_headers or (
            stripped.endswith(":") and stripped.rstrip(":") in header_aliases
        ):
            capturing = stripped
            if capturing not in {"Examples:", "Note:", "Notes:"}:
                kept.append(capturing)
            else:
                capturing = None
            continue
        if capturing is None:
            continue
        if capturing in {"Examples:", "Note:", "Notes:"}:
            continue
        if not stripped:
            capturing = None
            continue
        # Keep compact param/return lines only
        kept.append(" ".join(stripped.split()))

    # Prefer a single dense summary line when no structural blocks were found
    if len(kept) == 1:
        return kept[0][:200]
    return " | ".join(kept)[:280]


def _is_external_module(module: str) -> bool:
    if not module:
        return True
    root = module.lstrip(".").split(".", 1)[0]
    return root in _STDLIB_ROOTS


def _keep_import(source: str, *, is_relative: bool) -> bool:
    if is_relative or source.startswith("."):
        return True
    return not _is_external_module(source)


# ---------------------------------------------------------------------------
# Shared visitor scaffolding
# ---------------------------------------------------------------------------


@dataclass
class ScopeFrame:
    kind: str  # module | class | function
    name: str


@dataclass
class NodeVisitor:
    """Maintains module → class → method → nested-function scope while walking a CST."""

    source: bytes
    scope_stack: list[ScopeFrame] = field(default_factory=list)
    imports: list[ImportMeta] = field(default_factory=list)
    classes: list[ClassMeta] = field(default_factory=list)
    functions: list[FunctionMeta] = field(default_factory=list)
    _class_stack: list[ClassMeta] = field(default_factory=list)
    _fn_stack: list[FunctionMeta] = field(default_factory=list)

    FUNCTION_TYPES: ClassVar[frozenset[str]] = frozenset()
    CLASS_TYPES: ClassVar[frozenset[str]] = frozenset()

    @property
    def scope_path(self) -> str:
        return ".".join(frame.name for frame in self.scope_stack if frame.name)

    def push(self, kind: str, name: str) -> None:
        self.scope_stack.append(ScopeFrame(kind=kind, name=name))

    def pop(self) -> None:
        if self.scope_stack:
            self.scope_stack.pop()

    def visit(self, node: Node) -> None:
        raise NotImplementedError

    def _attach_function(self, fn: FunctionMeta) -> None:
        if self._fn_stack:
            self._fn_stack[-1].nested.append(fn)
        elif self._class_stack:
            self._class_stack[-1].methods.append(fn)
        else:
            self.functions.append(fn)

    def _attach_class(self, cls: ClassMeta) -> None:
        if self._class_stack:
            self._class_stack[-1].nested_classes.append(cls)
        else:
            self.classes.append(cls)


# ---------------------------------------------------------------------------
# Python visitor
# ---------------------------------------------------------------------------


class PythonNodeVisitor(NodeVisitor):
    FUNCTION_TYPES = frozenset({"function_definition", "async_function_definition"})
    CLASS_TYPES = frozenset({"class_definition"})

    def visit(self, node: Node) -> None:
        ntype = node.type
        if ntype == "import_statement":
            self._visit_import(node)
            return
        if ntype == "import_from_statement":
            self._visit_import_from(node)
            return
        if ntype == "class_definition":
            self._visit_class(node)
            return
        if ntype in self.FUNCTION_TYPES:
            self._visit_function(node)
            return
        if ntype == "decorated_definition":
            self._visit_decorated(node)
            return
        for child in node.named_children:
            self.visit(child)

    def _visit_import(self, node: Node) -> None:
        for child in node.named_children:
            if child.type in {"dotted_name", "aliased_import"}:
                raw = _text(child, self.source).split(" as ")[0].strip()
                if _keep_import(raw, is_relative=False):
                    self.imports.append(ImportMeta(source=raw, names=[], is_relative=False))

    def _visit_import_from(self, node: Node) -> None:
        module_node = _child_by_field(node, "module_name")
        # Count leading dots for relative imports (grammar may put them outside module_name)
        raw_full = _text(node, self.source)
        rel_prefix = ""
        if "from" in raw_full:
            after = raw_full.split("from", 1)[1].lstrip()
            while after.startswith("."):
                rel_prefix += "."
                after = after[1:]

        mod = _text(module_node, self.source) if module_node else ""
        source = f"{rel_prefix}{mod}" if rel_prefix or mod else rel_prefix
        is_relative = bool(rel_prefix) or source.startswith(".")
        if not _keep_import(source or ".", is_relative=is_relative):
            return

        names: list[str] = []
        for child in node.named_children:
            if child == module_node:
                continue
            if child.type in {"dotted_name", "aliased_import", "identifier"}:
                names.append(_text(child, self.source).split(" as ")[0].strip())
            elif child.type == "wildcard_import":
                names.append("*")
        self.imports.append(ImportMeta(source=source or ".", names=names, is_relative=is_relative))

    def _doc_from_body(self, body: Node | None) -> str | None:
        if body is None or not body.named_children:
            return None
        first = body.named_children[0]
        if first.type == "expression_statement" and first.named_children:
            expr = first.named_children[0]
            if expr.type == "string":
                return triage_docstring(_text(expr, self.source))
        return None

    def _type_text(self, node: Node | None) -> str | None:
        if node is None:
            return None
        return _text(node, self.source).replace(" ", "")

    def _parameters(self, params: Node | None) -> list[ArgumentMeta]:
        if params is None:
            return []
        args: list[ArgumentMeta] = []
        for child in params.named_children:
            if child.type == "identifier":
                name = _text(child, self.source)
                if name not in {"self", "cls"}:
                    args.append(ArgumentMeta(name=name))
            elif child.type in {
                "typed_parameter",
                "default_parameter",
                "typed_default_parameter",
            }:
                name_node = _child_by_field(child, "name") or (
                    child.named_children[0] if child.named_children else None
                )
                type_node = _child_by_field(child, "type")
                if name_node is None:
                    continue
                name = _text(name_node, self.source)
                if name in {"self", "cls"}:
                    continue
                args.append(ArgumentMeta(name=name, type_hint=self._type_text(type_node)))
            elif child.type in {"list_splat_pattern", "dictionary_splat_pattern"}:
                ident = next(
                    (c for c in child.named_children if c.type == "identifier"),
                    None,
                )
                if ident is not None:
                    prefix = "*" if child.type == "list_splat_pattern" else "**"
                    args.append(ArgumentMeta(name=f"{prefix}{_text(ident, self.source)}"))
            elif child.type == "list_splat":
                # typed *args
                ident = next(
                    (c for c in child.named_children if c.type == "identifier"),
                    None,
                )
                type_node = _child_by_field(child, "type")
                if ident is not None:
                    args.append(
                        ArgumentMeta(
                            name=f"*{_text(ident, self.source)}",
                            type_hint=self._type_text(type_node),
                        )
                    )
            elif child.type == "dictionary_splat":
                ident = next(
                    (c for c in child.named_children if c.type == "identifier"),
                    None,
                )
                type_node = _child_by_field(child, "type")
                if ident is not None:
                    args.append(
                        ArgumentMeta(
                            name=f"**{_text(ident, self.source)}",
                            type_hint=self._type_text(type_node),
                        )
                    )
        return args

    def _decorators(self, node: Node) -> list[str]:
        return [
            _text(child, self.source).lstrip("@").split("(")[0].strip()
            for child in node.children
            if child.type == "decorator"
        ]

    def _body_has_yield(self, body: Node | None) -> bool:
        if body is None:
            return False

        stack = list(body.children)
        while stack:
            cur = stack.pop()
            if cur.type in {"yield", "yield_statement"}:
                return True
            # Do not descend into nested function/class bodies for generator detection
            if cur.type in self.FUNCTION_TYPES | self.CLASS_TYPES:
                continue
            stack.extend(cur.children)
        return False

    def _is_async_node(self, node: Node) -> bool:
        if node.type == "async_function_definition":
            return True
        return any(child.type == "async" for child in node.children)

    def _visit_function(self, node: Node, *, extra_decorators: list[str] | None = None) -> None:
        name_node = _child_by_field(node, "name")
        name = _text(name_node, self.source) if name_node else "<anonymous>"
        body = _child_by_field(node, "body")
        is_method = bool(self._class_stack) and not self._fn_stack
        fn = FunctionMeta(
            name=name,
            args=self._parameters(_child_by_field(node, "parameters")),
            return_type=self._type_text(_child_by_field(node, "return_type")),
            docstring=self._doc_from_body(body),
            is_method=is_method,
            is_private=_is_private(name),
            is_async=self._is_async_node(node),
            is_generator=self._body_has_yield(body),
            decorators=(extra_decorators or []) + self._decorators(node),
            scope=self.scope_path,
        )
        self._attach_function(fn)
        self._fn_stack.append(fn)
        self.push("function", name)
        # Walk body for nested defs only — never capture statements/bodies
        if body is not None:
            for child in body.named_children:
                if child.type in self.FUNCTION_TYPES:
                    self._visit_function(child)
                elif child.type == "decorated_definition":
                    self._visit_decorated(child)
                elif child.type in self.CLASS_TYPES:
                    self._visit_class(child)
        self.pop()
        self._fn_stack.pop()

    def _visit_class(self, node: Node, *, extra_decorators: list[str] | None = None) -> None:
        del extra_decorators  # reserved for future class-decorator emission
        name_node = _child_by_field(node, "name")
        name = _text(name_node, self.source) if name_node else "<anonymous>"
        bases: list[str] = []
        superclasses = _child_by_field(node, "superclasses")
        if superclasses is not None:
            bases.extend(
                _text(child, self.source).replace(" ", "") for child in superclasses.named_children
            )
        body = _child_by_field(node, "body")
        cls = ClassMeta(
            name=name,
            bases=bases,
            docstring=self._doc_from_body(body),
            is_private=_is_private(name),
            scope=self.scope_path,
        )
        self._attach_class(cls)
        self._class_stack.append(cls)
        self.push("class", name)
        if body is not None:
            for child in body.named_children:
                if child.type in self.FUNCTION_TYPES:
                    self._visit_function(child)
                elif child.type == "decorated_definition":
                    self._visit_decorated(child)
                elif child.type in self.CLASS_TYPES:
                    self._visit_class(child)
        self.pop()
        self._class_stack.pop()

    def _visit_decorated(self, node: Node) -> None:
        decs = self._decorators(node)
        inner = next(
            (c for c in node.named_children if c.type in self.FUNCTION_TYPES | self.CLASS_TYPES),
            None,
        )
        if inner is None:
            return
        if inner.type in self.CLASS_TYPES:
            self._visit_class(inner, extra_decorators=decs)
        else:
            self._visit_function(inner, extra_decorators=decs)


# ---------------------------------------------------------------------------
# TypeScript visitor (structural; bodies ignored)
# ---------------------------------------------------------------------------


class TypeScriptNodeVisitor(NodeVisitor):
    FUNCTION_TYPES = frozenset(
        {
            "function_declaration",
            "method_definition",
            "abstract_method_signature",
        }
    )
    CLASS_TYPES = frozenset({"class_declaration"})

    def visit(self, node: Node) -> None:
        ntype = node.type
        if ntype == "import_statement":
            self._visit_import(node)
            return
        if ntype == "class_declaration":
            self._visit_class(node)
            return
        if ntype == "function_declaration":
            self._visit_function(node)
            return
        if ntype == "lexical_declaration":
            self._visit_lexical(node)
            return
        if ntype == "export_statement":
            for child in node.named_children:
                self.visit(child)
            return
        for child in node.named_children:
            self.visit(child)

    def _visit_import(self, node: Node) -> None:
        source_node = _child_by_field(node, "source")
        if source_node is None:
            return
        source = _text(source_node, self.source).strip("'\"")
        is_relative = source.startswith(".")
        if not _keep_import(source, is_relative=is_relative):
            return
        names: list[str] = []
        for child in node.named_children:
            if child.type == "import_clause":
                for part in child.named_children:
                    if part.type == "identifier":
                        names.append(_text(part, self.source))
                    elif part.type == "named_imports":
                        for spec in part.named_children:
                            if spec.type == "import_specifier":
                                name_n = _child_by_field(spec, "name")
                                if name_n is not None:
                                    names.append(_text(name_n, self.source))
        self.imports.append(ImportMeta(source=source, names=names, is_relative=is_relative))

    def _type_text(self, node: Node | None) -> str | None:
        if node is None:
            return None
        return _text(node, self.source).replace(" ", "").removeprefix(":").strip() or None

    def _params(self, params: Node | None) -> list[ArgumentMeta]:
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
            pattern = _child_by_field(child, "pattern") or next(
                (c for c in child.named_children if c.type in {"identifier", "object_pattern"}),
                None,
            )
            if pattern is None:
                continue
            name = _text(pattern, self.source)
            if child.type == "rest_parameter" and not name.startswith("..."):
                name = f"...{name}"
            args.append(
                ArgumentMeta(name=name, type_hint=self._type_text(_child_by_field(child, "type")))
            )
        return args

    def _visit_function(self, node: Node, *, is_method: bool = False) -> None:
        name_node = _child_by_field(node, "name")
        if name_node is None:
            return
        name = _text(name_node, self.source)
        fn = FunctionMeta(
            name=name,
            args=self._params(_child_by_field(node, "parameters")),
            return_type=self._type_text(_child_by_field(node, "return_type")),
            is_method=is_method or bool(self._class_stack),
            is_private=_is_private(name) or name.startswith("#"),
            is_async="async" in {c.type for c in node.children},
            scope=self.scope_path,
        )
        self._attach_function(fn)

    def _visit_lexical(self, node: Node) -> None:
        for declarator in (c for c in node.named_children if c.type == "variable_declarator"):
            name_n = _child_by_field(declarator, "name")
            value = _child_by_field(declarator, "value")
            if (
                name_n is None
                or value is None
                or value.type not in {"arrow_function", "function_expression"}
            ):
                continue
            name = _text(name_n, self.source)
            fn = FunctionMeta(
                name=name,
                args=self._params(_child_by_field(value, "parameters")),
                return_type=self._type_text(_child_by_field(value, "return_type")),
                is_private=_is_private(name),
                is_async="async" in {c.type for c in value.children},
                scope=self.scope_path,
            )
            self._attach_function(fn)

    def _visit_class(self, node: Node) -> None:
        name_node = _child_by_field(node, "name")
        name = _text(name_node, self.source) if name_node else "<anonymous>"
        bases: list[str] = []
        heritage = next((c for c in node.named_children if c.type == "class_heritage"), None)
        if heritage is not None:
            for child in heritage.named_children:
                if child.type in {"extends_clause", "implements_clause"}:
                    bases.extend(
                        _text(value, self.source).replace(" ", "") for value in child.named_children
                    )
        cls = ClassMeta(
            name=name,
            bases=bases,
            is_private=_is_private(name),
            scope=self.scope_path,
        )
        self._attach_class(cls)
        self._class_stack.append(cls)
        self.push("class", name)
        body = _child_by_field(node, "body")
        if body is not None:
            for child in body.named_children:
                if child.type in {
                    "method_definition",
                    "abstract_method_signature",
                }:
                    self._visit_function(child, is_method=True)
                elif child.type == "public_field_definition":
                    value = _child_by_field(child, "value")
                    name_n = _child_by_field(child, "name")
                    if (
                        value is not None
                        and name_n is not None
                        and value.type in {"arrow_function", "function_expression"}
                    ):
                        fn_name = _text(name_n, self.source)
                        self._attach_function(
                            FunctionMeta(
                                name=fn_name,
                                args=self._params(_child_by_field(value, "parameters")),
                                return_type=self._type_text(_child_by_field(value, "return_type")),
                                is_method=True,
                                is_private=_is_private(fn_name) or fn_name.startswith("#"),
                                scope=self.scope_path,
                            )
                        )
        self.pop()
        self._class_stack.pop()


# ---------------------------------------------------------------------------
# Go visitor
# ---------------------------------------------------------------------------


class GoNodeVisitor(NodeVisitor):
    def visit(self, node: Node) -> None:
        ntype = node.type
        if ntype == "import_declaration":
            self._visit_imports(node)
            return
        if ntype == "type_declaration":
            for child in node.named_children:
                if child.type == "type_spec":
                    self._visit_type_spec(child)
            return
        if ntype == "function_declaration":
            self._visit_function(node)
            return
        if ntype == "method_declaration":
            self._visit_method(node)
            return
        for child in node.named_children:
            self.visit(child)

    def _visit_imports(self, node: Node) -> None:
        specs: list[Node] = []
        for child in node.named_children:
            if child.type == "import_spec":
                specs.append(child)
            elif child.type == "import_spec_list":
                specs.extend(c for c in child.named_children if c.type == "import_spec")
        for spec in specs:
            path_n = _child_by_field(spec, "path")
            if path_n is None:
                continue
            source = _text(path_n, self.source).strip('"')
            # Keep only relative / local module paths (contain / and not known remote hosts
            # is too heuristic); prefer paths without dots-as-domain for stdlib.
            is_relative = source.startswith(".") or "/" not in source
            if "/" not in source and _is_external_module(source):
                continue
            if source.startswith("github.com/") or source.startswith("golang.org/"):
                continue
            self.imports.append(ImportMeta(source=source, names=[], is_relative=is_relative))

    def _type_text(self, node: Node | None) -> str | None:
        if node is None:
            return None
        return _text(node, self.source).replace(" ", "")

    def _params(self, params: Node | None) -> list[ArgumentMeta]:
        if params is None:
            return []
        args: list[ArgumentMeta] = []
        for child in params.named_children:
            if child.type != "parameter_declaration":
                continue
            type_node = _child_by_field(child, "type")
            type_hint = self._type_text(type_node)
            names = [c for c in child.named_children if c.type == "identifier" and c != type_node]
            if not names:
                args.append(ArgumentMeta(name="_", type_hint=type_hint))
            else:
                for name_n in names:
                    args.append(ArgumentMeta(name=_text(name_n, self.source), type_hint=type_hint))
        return args

    def _result_type(self, result: Node | None) -> str | None:
        if result is None:
            return None
        if result.type == "parameter_list":
            parts = [self._type_text(c) or "" for c in result.named_children]
            joined = ",".join(p for p in parts if p)
            return f"({joined})" if joined else None
        return self._type_text(result)

    def _visit_function(self, node: Node) -> None:
        name_node = _child_by_field(node, "name")
        name = _text(name_node, self.source) if name_node else "<anonymous>"
        self._attach_function(
            FunctionMeta(
                name=name,
                args=self._params(_child_by_field(node, "parameters")),
                return_type=self._result_type(_child_by_field(node, "result")),
                is_private=name[:1].islower() if name else False,
                scope=self.scope_path,
            )
        )

    def _visit_method(self, node: Node) -> None:
        receiver = _child_by_field(node, "receiver")
        recv_type: str | None = None
        if receiver is not None and receiver.named_children:
            param = receiver.named_children[0]
            recv_type = self._type_text(_child_by_field(param, "type"))
            if recv_type:
                recv_type = recv_type.lstrip("*")

        name_node = _child_by_field(node, "name")
        name = _text(name_node, self.source) if name_node else "<anonymous>"
        fn = FunctionMeta(
            name=name,
            args=self._params(_child_by_field(node, "parameters")),
            return_type=self._result_type(_child_by_field(node, "result")),
            is_method=True,
            is_private=name[:1].islower() if name else False,
            scope=recv_type or self.scope_path,
        )
        if recv_type:
            existing = next((c for c in self.classes if c.name == recv_type), None)
            if existing is None:
                existing = ClassMeta(
                    name=recv_type,
                    is_private=recv_type[:1].islower(),
                    scope=self.scope_path,
                )
                self.classes.append(existing)
            existing.methods.append(fn)
        else:
            self._attach_function(fn)

    def _visit_type_spec(self, node: Node) -> None:
        name_node = _child_by_field(node, "name")
        type_node = _child_by_field(node, "type")
        if name_node is None or type_node is None:
            return
        name = _text(name_node, self.source)
        bases: list[str] = []
        if type_node.type == "struct_type":
            field_list = next(
                (c for c in type_node.named_children if c.type == "field_declaration_list"),
                None,
            )
            if field_list is not None:
                for field_n in field_list.named_children:
                    if field_n.type != "field_declaration":
                        continue
                    names = [c for c in field_n.named_children if c.type == "field_identifier"]
                    type_n = _child_by_field(field_n, "type")
                    if not names and type_n is not None:
                        bases.append(self._type_text(type_n) or "")
        elif type_node.type != "interface_type":
            return

        existing = next((c for c in self.classes if c.name == name), None)
        if existing is None:
            self.classes.append(
                ClassMeta(
                    name=name,
                    bases=[b for b in bases if b],
                    is_private=name[:1].islower(),
                    scope=self.scope_path,
                )
            )
        else:
            existing.bases = [b for b in bases if b] or existing.bases


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_file(parsed: ParsedFile) -> FileMeta:
    """Extract structural metadata (bodies stripped) with scoped hierarchy."""
    module_id = _module_id_from_path(parsed.relative_path)
    visitor: NodeVisitor

    if parsed.language is Language.PYTHON:
        visitor = PythonNodeVisitor(source=parsed.source)
    elif parsed.language is Language.TYPESCRIPT:
        visitor = TypeScriptNodeVisitor(source=parsed.source)
    elif parsed.language is Language.GO:
        visitor = GoNodeVisitor(source=parsed.source)
    else:
        return FileMeta(path=parsed.relative_path, language=parsed.language, module_id=module_id)

    visitor.push("module", module_id)
    visitor.visit(parsed.root_node)
    visitor.pop()

    return FileMeta(
        path=parsed.relative_path,
        language=parsed.language,
        module_id=module_id,
        imports=visitor.imports,
        classes=visitor.classes,
        functions=visitor.functions,
    )


def extract_all(parsed_files: list[ParsedFile]) -> list[FileMeta]:
    """Extract metadata for every parsed file."""
    return [extract_file(parsed) for parsed in parsed_files]
