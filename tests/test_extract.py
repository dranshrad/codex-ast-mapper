"""Integration tests against Tree-sitter extraction edge cases."""

from __future__ import annotations

from pathlib import Path

from src.ast_extractor import extract_file, triage_docstring
from src.encoder import encode_map
from src.parser import parse_file, walk_and_parse

EDGE_CASE_SOURCE = '''\
"""Module for identity resolution pipelines."""
from __future__ import annotations

import os
import sys
from typing import Optional, Union
from .models import Identity, RepoMap
from ..core.types import Node

class Base:
    pass

class User(Base):
    """Account owner with sync lifecycle.

    This conversational paragraph must be discarded by docstring triage
    because it burns tokens without structural value.

    Args:
        id: Primary key
    Returns:
        None after remote sync
    """

    def __init__(self, id: int) -> None:
        self.id = id

    async def sync(
        self,
        id: int,
        *args: str,
        **kwargs: dict[str, Union[int, Identity]],
    ) -> None:
        """Push remote state.

        Extra chatter that should not survive triage.
        """
        print(id, args, kwargs)
        await self._helper()

    def _helper(self) -> str:
        return "x"

    def stream(self, limit: int = 10) -> dict[str, Union[int, Identity]]:
        """Yield batched identity rows.

        Yields:
            Mapping chunks
        """
        for i in range(limit):
            yield {"n": i}

def load(path: str) -> User:
    """Load a user from disk."""
    def _inner(p: str) -> User:
        return User(1)
    return _inner(path)
'''


def test_parse_and_extract_python_edge_cases(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text(EDGE_CASE_SOURCE, encoding="utf-8")

    parsed = parse_file(source, tmp_path)
    assert parsed is not None
    meta = extract_file(parsed)

    assert meta.language.value == "python"
    assert meta.module_id == "sample"

    # Third-party / stdlib filtered; relative dependency anchors retained
    sources = {imp.source for imp in meta.imports}
    assert "os" not in sources
    assert "sys" not in sources
    assert "typing" not in sources
    assert any(s.startswith(".") for s in sources)
    relative = next(i for i in meta.imports if i.source.endswith("models") or ".models" in i.source)
    assert "Identity" in relative.names or "RepoMap" in relative.names

    user = next(c for c in meta.classes if c.name == "User")
    assert user.bases == ["Base"]
    # Docstring triage: summary + Args/Returns, no conversational filler
    assert user.docstring is not None
    assert "Account owner" in user.docstring
    assert "conversational paragraph" not in (user.docstring or "")
    assert "Args:" in (user.docstring or "") or "id:" in (user.docstring or "")

    init = next(m for m in user.methods if m.name == "__init__")
    assert [(a.name, a.type_hint) for a in init.args] == [("id", "int")]

    sync = next(m for m in user.methods if m.name == "sync")
    assert sync.is_async
    arg_names = [a.name for a in sync.args]
    assert "id" in arg_names
    assert any(a.name.startswith("*args") or a.name == "*args" for a in sync.args)
    assert any(a.name.startswith("**kwargs") or a.name == "**kwargs" for a in sync.args)
    # Complex generic preserved on kwargs or id
    assert sync.return_type == "None"
    assert "Union" in (next(a.type_hint for a in sync.args if a.name.startswith("**")) or "")

    helper = next(m for m in user.methods if m.name == "_helper")
    assert helper.is_private

    stream = next(m for m in user.methods if m.name == "stream")
    assert stream.is_generator
    assert stream.return_type is not None
    assert "dict[str,Union[int,Identity]]" in stream.return_type.replace(" ", "")

    load_fn = next(f for f in meta.functions if f.name == "load")
    assert any(n.name == "_inner" for n in load_fn.nested)

    xml = encode_map([meta])
    assert "print" not in xml  # body stripped
    assert '<c name="User"' in xml
    assert "<init " in xml
    assert 'a="1"' in xml  # async marker
    assert 'g="1"' in xml  # generator marker
    assert '<m id="sample">' in xml


def test_docstring_triage_summary_only() -> None:
    raw = '''"""Sync remote state.

    Long explanation that should be dropped entirely.
    """'''
    assert triage_docstring(raw) == "Sync remote state."


def test_walk_respects_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("secret/\n", encoding="utf-8")
    keep = tmp_path / "keep.py"
    keep.write_text("def ok() -> None:\n    pass\n", encoding="utf-8")
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    (secret_dir / "hidden.py").write_text("def no() -> None:\n    pass\n", encoding="utf-8")

    parsed = walk_and_parse(tmp_path)
    paths = {p.relative_path for p in parsed}
    assert "keep.py" in paths
    assert "secret/hidden.py" not in paths


def test_symlink_cycle_does_not_hang(tmp_path: Path) -> None:
    """Circular directory symlinks must not infinite-loop the walker."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "ok.py").write_text("def ok() -> int:\n    return 1\n", encoding="utf-8")
    # a/loop -> b, b/loop -> a
    (a / "loop").symlink_to(b, target_is_directory=True)
    (b / "loop").symlink_to(a, target_is_directory=True)

    parsed = walk_and_parse(tmp_path)
    paths = {p.relative_path for p in parsed}
    assert "a/ok.py" in paths
    assert len(paths) == 1
