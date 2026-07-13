"""Tiktoken wrapper for cumulative token budgets."""

from __future__ import annotations

from dataclasses import dataclass, field

import tiktoken


@dataclass
class TokenBudget:
    """Tracks cumulative token usage against a hard ceiling."""

    max_tokens: int
    encoding_name: str = "cl100k_base"
    _encoding: tiktoken.Encoding = field(init=False, repr=False)
    _used: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_encoding", tiktoken.get_encoding(self.encoding_name))

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens - self._used)

    @property
    def exceeded(self) -> bool:
        return self._used > self.max_tokens

    def count(self, text: str) -> int:
        """Return the token count for ``text`` without mutating state."""
        return len(self._encoding.encode(text))

    def reset(self) -> None:
        self._used = 0

    def set_used(self, tokens: int) -> None:
        self._used = max(0, tokens)

    def measure(self, text: str) -> int:
        """Count tokens for ``text`` and store as the current usage."""
        self._used = self.count(text)
        return self._used

    def fits(self, text: str) -> bool:
        return self.count(text) <= self.max_tokens
