"""Backwards-compatible shim.

The LLM factory now lives in :mod:`app.llm.factory` alongside the structured
output helpers used by the agents. Import from there in new code.
"""

from __future__ import annotations

from app.llm.factory import build_chat_llm, get_chat_llm

__all__ = ["build_chat_llm", "get_chat_llm"]
