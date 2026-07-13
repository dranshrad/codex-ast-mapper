"""Back-compat facade over the XML exporter and budget pipeline."""

from __future__ import annotations

from src.exporters.pipeline import ExportResult, export_within_budget
from src.exporters.xml import EncodeFlags, encode_map, export_xml
from src.models import FileMeta, LLMMode, PruneLevel
from src.tokenizer_util import TokenBudget

__all__ = [
    "EncodeFlags",
    "EncodeResult",
    "encode_map",
    "encode_within_budget",
    "export_xml",
]


class EncodeResult:
    """Legacy result shape expected by older tests and call sites."""

    __slots__ = ("prune_level", "tokens", "within_budget", "xml")

    def __init__(
        self,
        xml: str,
        tokens: int,
        prune_level: PruneLevel,
        within_budget: bool,
    ) -> None:
        self.xml = xml
        self.tokens = tokens
        self.prune_level = prune_level
        self.within_budget = within_budget

    @classmethod
    def from_export(cls, result: ExportResult) -> EncodeResult:
        return cls(
            xml=result.content,
            tokens=result.tokens,
            prune_level=result.prune_level,
            within_budget=result.within_budget,
        )


def encode_within_budget(
    files: list[FileMeta],
    budget: TokenBudget,
    *,
    mode: LLMMode = "developer",
) -> EncodeResult:
    """Encode to XML within ``budget``, using importance-aware dropping."""
    result = export_within_budget(files, budget, fmt="xml", mode=mode)
    return EncodeResult.from_export(result)
