"""Shared Claude (Anthropic) helpers for Croar Pilot + generation.

A single async Anthropic client + two helpers:
- ``claude_complete`` — free-form text out.
- ``claude_json``     — JSON out; strips code fences / prose and returns just the JSON
  substring so existing ``json.loads`` call-sites keep working (Anthropic has no
  OpenAI-style ``response_format=json_object``, so we enforce JSON via the prompt/system
  and extract it here).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from anthropic import Anthropic, AsyncAnthropic

from app.core.settings import get_settings

_settings = get_settings()
_client: AsyncAnthropic | None = (
    AsyncAnthropic(api_key=_settings.anthropic_api_key) if _settings.anthropic_api_key else None
)
_sync_client: Anthropic | None = (
    Anthropic(api_key=_settings.anthropic_api_key) if _settings.anthropic_api_key else None
)

_JSON_SYSTEM = "You output ONLY valid JSON — no prose, no explanations, no markdown code fences."


def _extract_json(text: str) -> str:
    """Return just the JSON object/array from a model reply."""
    t = (text or "").strip()
    # \s*+ is possessive: with re.DOTALL the following (.*?) can match whitespace too, so a
    # plain \s* left the two overlapping and a reply with an unclosed fence backtracked
    # quadratically. Nothing can be gained by giving those characters back, so don't allow it.
    fence = re.search(r"```(?:json)?\s*+(.*?)```", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    # Outermost {...} or [...] — whichever appears and is well-formed at the ends.
    candidates = []
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = t.find(open_c), t.rfind(close_c)
        if i != -1 and j > i:
            candidates.append((i, t[i : j + 1]))
    if candidates:
        # Prefer the one that starts earliest (usually the intended top-level value).
        candidates.sort(key=lambda c: c[0])
        return candidates[0][1]
    return t or "{}"


async def _meter_llm(resp: Any, action: str = "llm_call") -> None:
    """Best-effort: charge the current company for one AI call, tokens in meta.

    Reads the per-request billing context (set in the auth dependency) so all AI usage is
    metered centrally without each endpoint wiring it up. Never raises.
    """
    try:
        from app.services.enterprise import credit_service as cs

        ctx = cs.get_billing_context()
        if ctx is None or ctx.company_id is None:
            return
        usage = getattr(resp, "usage", None)
        await cs.record_ai_usage(
            ctx.company_id,
            int(getattr(usage, "input_tokens", 0) or 0),
            int(getattr(usage, "output_tokens", 0) or 0),
            action=action,
            user_id=ctx.user_id,
            description="AI generation",
            meta={"model": _settings.anthropic_model},
        )
    except Exception:  # metering must never break generation
        pass


async def claude_complete(prompt: str, *, system: str | None = None, max_tokens: int = 4096) -> str:
    if _client is None:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    resp = await _client.messages.create(
        model=_settings.anthropic_model,
        max_tokens=max_tokens,
        system=system or "You are a helpful assistant.",
        messages=[{"role": "user", "content": prompt}],
    )
    await _meter_llm(resp)
    return "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")


async def claude_json(prompt: str, *, system: str | None = None, max_tokens: int = 4096) -> str:
    text = await claude_complete(prompt, system=system or _JSON_SYSTEM, max_tokens=max_tokens)
    return _extract_json(text)


# ---------------------------------------------------------------------------------------
# OpenAI-compatible shims — drop-in replacements for AsyncOpenAI / OpenAI whose
# `chat.completions.create(...)` routes to Claude and returns an OpenAI-shaped response
# (`resp.choices[0].message.content`). Lets the many existing GPT call-sites move to Claude
# without rewriting each one. `response_format={"type":"json_object"}` maps to claude_json.
# ---------------------------------------------------------------------------------------
def _split_messages(messages: list[dict[str, Any]] | None) -> tuple[str | None, str]:
    msgs = messages or []
    system = "\n\n".join(str(m.get("content", "")) for m in msgs if m.get("role") == "system") or None
    user = "\n\n".join(str(m.get("content", "")) for m in msgs if m.get("role") != "system")
    return system, user


def _wants_json(response_format: Any) -> bool:
    return isinstance(response_format, dict) and response_format.get("type") in ("json_object", "json_schema")


@dataclass
class _ShimMessage:
    content: str


class _ShimChoice:
    def __init__(self, content: str) -> None:
        self.message = _ShimMessage(content)


class _ShimResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_ShimChoice(content)]


class _AsyncCompletions:
    async def create(
        self,
        *,
        messages: list[dict[str, Any]] | None = None,
        response_format: Any = None,
        max_tokens: int | None = None,
        **_: Any,
    ) -> _ShimResponse:
        system, user = _split_messages(messages)
        if _wants_json(response_format):
            content = await claude_json(user, system=system, max_tokens=max_tokens or 4096)
        else:
            content = await claude_complete(user, system=system, max_tokens=max_tokens or 2048)
        return _ShimResponse(content)


class _SyncCompletions:
    def create(
        self,
        *,
        messages: list[dict[str, Any]] | None = None,
        response_format: Any = None,
        max_tokens: int | None = None,
        **_: Any,
    ) -> _ShimResponse:
        if _sync_client is None:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        system, user = _split_messages(messages)
        resp = _sync_client.messages.create(
            model=_settings.anthropic_model,
            max_tokens=max_tokens or 4096,
            system=system or "You are a helpful assistant.",
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
        return _ShimResponse(_extract_json(text) if _wants_json(response_format) else text)


class _AsyncChat:
    completions = _AsyncCompletions()


class _SyncChat:
    completions = _SyncCompletions()


class AsyncClaudeOpenAI:
    """Drop-in for `AsyncOpenAI` — routes chat.completions.create to Claude (async)."""

    def __init__(self, *_: Any, **__: Any) -> None:
        self.chat = _AsyncChat()
        # Some callers guard with `if not client.api_key`; expose the Anthropic key here.
        self.api_key = _settings.anthropic_api_key


class SyncClaudeOpenAI:
    """Drop-in for `OpenAI` — routes chat.completions.create to Claude (sync)."""

    def __init__(self, *_: Any, **__: Any) -> None:
        self.chat = _SyncChat()
        self.api_key = _settings.anthropic_api_key
