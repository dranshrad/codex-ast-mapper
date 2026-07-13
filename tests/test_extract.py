"""Integration tests against Tree-sitter extraction."""

from __future__ import annotations

from pathlib import Path

from src.ast_extractor import extract_file
from src.encoder import encode_map
from src.parser import parse_file, walk_and_parse


def test_parse_and_extract_python(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        '''\
"""Module doc."""
from typing import Optional

class Base:
    pass

class User(Base):
    """Account owner."""

    def sync(self, id: int) -> None:
        """Push remote state."""
        print(id)

    def _helper(self) -> str:
        return "x"

def load(path: str) -> User:
    return User()
''',
        encoding="utf-8",
    )

    parsed = parse_file(source, tmp_path)
    assert parsed is not None
    meta = extract_file(parsed)

    assert meta.language.value == "python"
    assert any(i.startswith("typing") for i in meta.imports)
    assert {c.name for c in meta.classes} >= {"User", "Base"}
    user = next(c for c in meta.classes if c.name == "User")
    assert user.bases == ["Base"]
    assert user.docstring == "Account owner."
    sync = next(m for m in user.methods if m.name == "sync")
    assert [(a.name, a.type_hint) for a in sync.args] == [("id", "int")]
    assert sync.return_type == "None"
    assert sync.docstring == "Push remote state."
    helper = next(m for m in user.methods if m.name == "_helper")
    assert helper.is_private
    assert any(f.name == "load" for f in meta.functions)

    xml = encode_map([meta])
    assert "print" not in xml  # body stripped
    assert '<class name="User"' in xml


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
