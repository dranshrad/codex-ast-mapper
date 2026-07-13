"""Exporter and mode pipeline tests."""

from __future__ import annotations

import json

from src.encoder import encode_map, encode_within_budget
from src.exporters.json import export_json
from src.exporters.mermaid import export_mermaid
from src.exporters.modes import mode_flags
from src.exporters.pipeline import export_within_budget
from src.graph import build_repo_graph
from src.models import (
    ArgumentMeta,
    ClassMeta,
    FileMeta,
    FunctionMeta,
    ImportMeta,
    Language,
)
from src.tokenizer_util import TokenBudget


def _sample_files() -> list[FileMeta]:
    return [
        FileMeta(
            path="pkg/user.py",
            language=Language.PYTHON,
            module_id="pkg.user",
            imports=[
                ImportMeta(source=".models", names=["User", "RepoMap"], is_relative=True),
            ],
            classes=[
                ClassMeta(
                    name="User",
                    bases=["Base"],
                    docstring="A user account entity with sync helpers.",
                    methods=[
                        FunctionMeta(
                            name="__init__",
                            args=[ArgumentMeta("id", "int")],
                            return_type="None",
                            is_method=True,
                        ),
                        FunctionMeta(
                            name="sync",
                            args=[ArgumentMeta("id", "int")],
                            return_type="None",
                            docstring="Synchronize remote state.",
                            is_method=True,
                            is_async=True,
                        ),
                        FunctionMeta(
                            name="_cache_key",
                            args=[ArgumentMeta("id", "int")],
                            return_type="str",
                            is_method=True,
                            is_private=True,
                        ),
                    ],
                )
            ],
            functions=[
                FunctionMeta(
                    name="load",
                    args=[ArgumentMeta("path", "str")],
                    return_type="Optional[User]",
                    docstring="Load a user from disk.",
                )
            ],
        ),
        FileMeta(
            path="pkg/models.py",
            language=Language.PYTHON,
            module_id="pkg.models",
            classes=[ClassMeta(name="Base"), ClassMeta(name="RepoMap")],
        ),
        FileMeta(
            path="pkg/helpers.py",
            language=Language.PYTHON,
            module_id="pkg.helpers",
            imports=[],
            functions=[
                FunctionMeta(
                    name="_internal",
                    args=[ArgumentMeta("x", "int")],
                    return_type="int",
                    is_private=True,
                )
            ],
        ),
    ]


def test_json_includes_edges_and_importance() -> None:
    files = _sample_files()
    graph = build_repo_graph(files)
    payload = json.loads(export_json(files, graph=graph))
    assert payload["version"] == 2
    assert "edges" in payload
    assert "importance" in payload
    assert any(e["kind"] == "imports" for e in payload["edges"])
    assert "pkg.models" in payload["importance"]


def test_mermaid_contains_modules_and_imports() -> None:
    text = export_mermaid(_sample_files())
    assert text.startswith("flowchart LR")
    assert "pkg_user" in text or "pkg.user" in text
    assert "-->" in text


def test_planning_mode_smaller_than_developer() -> None:
    files = _sample_files()
    budget = TokenBudget(max_tokens=50_000)
    dev = export_within_budget(files, budget, fmt="xml", mode="developer")
    plan = export_within_budget(files, TokenBudget(max_tokens=50_000), fmt="xml", mode="planning")
    assert plan.tokens < dev.tokens
    assert mode_flags("planning").include_doc is False


def test_importance_drops_helpers_before_hubs() -> None:
    files = _sample_files()
    graph = build_repo_graph(files)
    # Force file dropping
    result = encode_within_budget(files, TokenBudget(max_tokens=80))
    assert result.within_budget
    # helpers should be gone before models (hub)
    if "pkg.helpers" in result.xml:
        assert "pkg.models" in result.xml
    assert graph.importance["pkg.helpers"] <= graph.importance["pkg.models"]


def test_xml_still_hyper_dense() -> None:
    xml = encode_map(_sample_files())
    assert xml.startswith("<repo>")
    assert '<m id="pkg.user">' in xml
    assert "<file " not in xml
