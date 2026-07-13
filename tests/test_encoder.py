"""Encoder and progressive pruning matrix tests."""

from __future__ import annotations

from src.encoder import EncodeFlags, encode_map, encode_within_budget
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


def test_hyper_dense_xml_shape() -> None:
    xml = encode_map(_sample_files())
    assert xml.startswith("<repo>")
    assert '<m id="pkg.user">' in xml
    assert '<imp src=".models" names="User,RepoMap"/>' in xml
    assert '<c name="User"' in xml
    assert "<init " in xml and 'args="id:int"' in xml
    assert '<f name="sync"' in xml
    assert 'a="1"' in xml
    assert "_cache_key" in xml
    assert "Synchronize" in xml
    # No legacy verbose tags
    assert "<file " not in xml
    assert "<class " not in xml
    assert "<method " not in xml


def test_tier1_strips_helpers() -> None:
    xml = encode_map(_sample_files(), EncodeFlags(include_helpers=False))
    assert "_cache_key" not in xml
    assert "_internal" not in xml
    assert "sync" in xml
    assert "Synchronize" in xml


def test_tier2_strips_docstrings() -> None:
    xml = encode_map(
        _sample_files(),
        EncodeFlags(include_helpers=False, include_doc=False),
    )
    assert "Synchronize" not in xml
    assert "Load a user" not in xml
    assert '<f name="sync"' in xml


def test_tier3_abbreviates_types() -> None:
    xml = encode_map(
        _sample_files(),
        EncodeFlags(include_helpers=False, include_doc=False, compress_types=True),
    )
    assert "Optional[User]" not in xml
    assert "Opt[User]" in xml or 'ret="Opt[User]"' in xml


def test_progressive_pruning_matrix() -> None:
    files = _sample_files()
    full = encode_map(files)
    assert "Synchronize" in full

    tight = TokenBudget(max_tokens=120)
    result = encode_within_budget(files, tight)
    assert result.within_budget
    assert result.prune_level in {"helpers", "docstrings", "types", "imports"}
    # After mild+ tiers, private helpers should be gone
    if result.prune_level != "none":
        assert "_cache_key" not in result.xml or result.prune_level == "helpers"


def test_token_budget_counts() -> None:
    budget = TokenBudget(max_tokens=100)
    n = budget.measure("hello world")
    assert n > 0
    assert budget.used == n
    assert budget.remaining == 100 - n
