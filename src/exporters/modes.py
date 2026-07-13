"""LLM context modes → EncodeFlags presets."""

from __future__ import annotations

from src.exporters.xml import EncodeFlags
from src.models import LLMMode


def mode_flags(mode: LLMMode) -> EncodeFlags:
    """
    Return baseline encode flags for an LLM workflow mode.

    Modes compose with the progressive prune matrix; they set the *starting*
    inclusion policy before token-budget tiers tighten further.
    """
    if mode == "developer":
        return EncodeFlags()
    if mode == "review":
        return EncodeFlags(include_helpers=False)
    if mode == "planning":
        return EncodeFlags(
            include_helpers=False,
            include_doc=False,
            compress_types=True,
        )
    if mode == "docs":
        return EncodeFlags(include_helpers=False, include_types=False)
    # refactor — keep types/inheritance signal, drop helpers and long docs early
    return EncodeFlags(include_helpers=False, include_doc=False)
