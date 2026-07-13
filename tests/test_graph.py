"""Repository graph construction tests."""

from __future__ import annotations

from src.graph import build_repo_graph, ranked_modules, top_hubs
from src.models import (
    ClassMeta,
    EdgeKind,
    FileMeta,
    FunctionMeta,
    ImportMeta,
    Language,
)
from src.paths import resolve_relative_import


def test_resolve_relative_import() -> None:
    assert resolve_relative_import("pkg.user", ".models") == "pkg.models"
    assert resolve_relative_import("pkg.sub.user", "..models") == "pkg.models"
    assert resolve_relative_import("pkg.user", "pkg.other") == "pkg.other"


def _fixture_files() -> list[FileMeta]:
    return [
        FileMeta(
            path="pkg/models.py",
            language=Language.PYTHON,
            module_id="pkg.models",
            classes=[ClassMeta(name="Base"), ClassMeta(name="Identity")],
            functions=[FunctionMeta(name="factory")],
        ),
        FileMeta(
            path="pkg/user.py",
            language=Language.PYTHON,
            module_id="pkg.user",
            imports=[
                ImportMeta(source=".models", names=["Identity", "Base"], is_relative=True),
            ],
            classes=[
                ClassMeta(
                    name="User",
                    bases=["Base"],
                    methods=[FunctionMeta(name="sync", is_method=True)],
                )
            ],
        ),
        FileMeta(
            path="pkg/helpers.py",
            language=Language.PYTHON,
            module_id="pkg.helpers",
            functions=[FunctionMeta(name="_internal", is_private=True)],
        ),
    ]


def test_build_repo_graph_resolves_imports_and_inherits() -> None:
    graph = build_repo_graph(_fixture_files())
    assert "pkg.models" in graph.modules
    assert "pkg.user" in graph.modules

    import_edges = [e for e in graph.edges if e.kind is EdgeKind.IMPORTS]
    assert any(e.source == "pkg.user" and e.target == "pkg.models" for e in import_edges)

    inherit_edges = [e for e in graph.edges if e.kind is EdgeKind.INHERITS]
    assert any(e.source == "pkg.user:User" and e.target == "pkg.models:Base" for e in inherit_edges)

    assert "pkg.user:User.sync" in graph.symbols
    assert graph.modules["pkg.user"].imports[0].resolved == "pkg.models"


def test_importance_ranks_hubs_above_helpers() -> None:
    graph = build_repo_graph(_fixture_files())
    # models is imported by user → higher inbound than helpers
    assert graph.importance["pkg.models"] > graph.importance["pkg.helpers"]
    drop_order = ranked_modules(graph, ascending=True)
    assert drop_order[0] == "pkg.helpers"
    hubs = top_hubs(graph, n=2)
    assert hubs[0][0] in {"pkg.models", "pkg.user"}
