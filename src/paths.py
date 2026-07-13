"""Shared path / module-id helpers."""

from __future__ import annotations


def module_id_from_path(relative_path: str) -> str:
    """Convert ``src/parser.py`` → ``src.parser`` (drops ``__init__`` segments)."""
    path = relative_path.replace("\\", "/")
    for suffix in (".pyi", ".py", ".tsx", ".ts", ".go"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    parts = [p for p in path.split("/") if p and p != "__init__"]
    return ".".join(parts)


def parent_package(module_id: str) -> str:
    """Return the parent dotted package, or ``\"\"`` for top-level modules."""
    if "." not in module_id:
        return ""
    return module_id.rsplit(".", 1)[0]


def resolve_relative_import(current_module: str, import_source: str) -> str:
    """
    Resolve a relative import source against ``current_module``.

    Examples::

        resolve_relative_import("pkg.user", ".models") -> "pkg.models"
        resolve_relative_import("pkg.sub.user", "..models") -> "pkg.models"
        resolve_relative_import("pkg.user", "pkg.models") -> "pkg.models"
    """
    if not import_source.startswith("."):
        return import_source.lstrip(".")

    dots = 0
    while dots < len(import_source) and import_source[dots] == ".":
        dots += 1
    remainder = import_source[dots:]

    parts = current_module.split(".") if current_module else []
    if dots > len(parts):
        base_parts: list[str] = []
    else:
        # N leading dots → drop N segments from the current module id
        base_parts = parts[: len(parts) - dots]

    if remainder:
        base_parts.extend(p for p in remainder.split(".") if p)
    return ".".join(base_parts)
