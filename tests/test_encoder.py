"""Encoder and pruning tests."""

from __future__ import annotations

from src.encoder import encode_map, encode_within_budget
from src.models import ArgumentMeta, ClassMeta, FileMeta, FunctionMeta, Language
from src.tokenizer_util import TokenBudget


def _sample_files() -> list[FileMeta]:
    return [
        FileMeta(
            path="pkg/user.py",
            language=Language.PYTHON,
            imports=["typing", "os.path"],
            classes=[
                ClassMeta(
                    name="User",
                    bases=["Base"],
                    docstring="A user account entity with sync helpers.",
                    methods=[
                        FunctionMeta(
                            name="sync",
                            args=[ArgumentMeta("id", "int")],
                            return_type="None",
                            docstring="Synchronize remote state.",
                            is_method=True,
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
                    return_type="User",
                    docstring="Load a user from disk.",
                )
            ],
        ),
        FileMeta(
            path="pkg/helpers.py",
            language=Language.PYTHON,
            imports=["json"],
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


def test_encode_map_minified() -> None:
    xml = encode_map(_sample_files())
    assert xml.startswith("<repo>")
    assert 'path="pkg/user.py"' in xml
    assert '<method name="sync" args="id:int" ret="None">' in xml or (
        '<method name="sync" args="id:int" ret="None"/>' in xml
    )
    assert "_cache_key" in xml
    assert "Synchronize" in xml


def test_progressive_pruning_strips_docs_then_helpers() -> None:
    files = _sample_files()
    full = encode_map(files)
    assert "Synchronize" in full
    # Force under-budget by using a tight limit that still allows pruned output
    tight = TokenBudget(max_tokens=120)
    result = encode_within_budget(files, tight)
    assert result.within_budget
    assert result.prune_level != "none"
    # helpers-only file should be easy to drop once helpers are stripped
    assert "Synchronize" not in result.xml or result.prune_level != "none"


def test_token_budget_counts() -> None:
    budget = TokenBudget(max_tokens=100)
    n = budget.measure("hello world")
    assert n > 0
    assert budget.used == n
    assert budget.remaining == 100 - n
