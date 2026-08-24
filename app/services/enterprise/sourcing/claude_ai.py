"""Claude-powered candidate sourcing.

Replaces the 27 scraper providers with a single LLM provider: given the recruiter's
search text (role / skills / location), Claude uses the web-search tool to find real
public professional profiles (GitHub, LinkedIn, personal sites, Stack Overflow, dev.to,
…) and returns them in the standard SourcingProfile shape.

If web search isn't available on the account, it falls back to Claude's own knowledge and
flags each result as an AI suggestion (so the UI can show it's model-derived, not scraped).

The provider's ``search`` is synchronous on purpose — SourcingService runs providers in a
thread (they were blocking ``requests`` scrapers), so we use the sync Anthropic client.
"""

from __future__ import annotations

import json
import re
from typing import Any

from anthropic import Anthropic
from loguru import logger

from app.core.settings import get_settings

from .base import SourcingProvider

_MAX_RESULTS = 20
_MIN_TARGET = 15  # aim for a healthy batch even when the caller's page size is small
_MAX_ROUNDS = 3  # top-up rounds when a single call comes up short of the target
_WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 6}
_SYSTEM_SEARCH = (
    "You are a candidate-sourcing web researcher for a recruiter. You run multiple web "
    "searches to find real, public professional profiles and list the people you find. "
    "Be thorough — vary your searches to surface as many distinct real candidates as asked."
)
_SYSTEM_JSON = (
    "You convert a candidate list into strict JSON. Your entire reply must be a single JSON "
    "array and nothing else — no prose, no markdown fences."
)


def _salvage_objects(raw: str) -> list[dict[str, Any]]:
    """Collect complete top-level {...} objects even if the array was truncated."""
    objs: list[dict[str, Any]] = []
    depth = 0
    start: int | None = None
    for i, ch in enumerate(raw):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    objs.append(json.loads(raw[start : i + 1]))
                except json.JSONDecodeError:
                    pass
                start = None
    return objs


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    """Pull profiles out of the model's text output — robust to prose wrappers and to a
    JSON array that got truncated by the token limit (salvages complete objects)."""
    if not text:
        return []
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    raw = fence.group(1) if fence else None
    if not raw:
        span = re.search(r"\[.*\]", text, re.DOTALL)  # first '[' .. last ']'
        raw = span.group(0) if span else None
    if not raw:
        # No array markers at all — try salvaging bare objects from the whole text.
        return _salvage_objects(text)
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return _salvage_objects(raw)


def _to_profile(item: dict[str, Any], location: str | None, ai_sourced: bool) -> dict[str, Any] | None:
    name = str(item.get("full_name") or item.get("name") or "").strip()
    url = str(item.get("profile_url") or item.get("url") or "").strip()
    if not name or not url:
        return None
    skills = item.get("skills") or []
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]
    return {
        "full_name": name,
        "platform": "Claude",
        "profile_url": url,
        "headline": item.get("headline") or item.get("title"),
        "location": item.get("location") or location,
        "company": item.get("company"),
        "email": item.get("email"),
        "skills": skills,
        "ai_summary": item.get("ai_summary") or item.get("summary"),
        "ai_sourced": ai_sourced,
        "raw_data": item,
    }


class ClaudeSourcingProvider(SourcingProvider):
    @property
    def platform_name(self) -> str:
        return "Claude"

    def _call(self, prompt: str, *, system: str, use_web_search: bool) -> str:
        settings = get_settings()
        client = Anthropic(api_key=settings.anthropic_api_key)
        kwargs: dict[str, Any] = {
            "model": settings.sourcing_model,
            "max_tokens": 8000,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        if use_web_search:
            kwargs["tools"] = [_WEB_SEARCH_TOOL]
        resp = client.messages.create(**kwargs)
        # Concatenate the text blocks (web-search responses interleave tool_use blocks).
        return "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")

    def _search_prompt(
        self, n: int, query: str, location: str | None, exclude: list[str], *, web: bool = True
    ) -> str:
        loc = f" located in or near {location}" if location else ""
        avoid = ""
        if exclude:
            avoid = (
                f"\nDo NOT repeat these already-found people; find DIFFERENT ones: {', '.join(exclude[:40])}."
            )
        how = (
            "Use web search to find"
            if web
            else "From your own knowledge (web search is unavailable), suggest"
        )
        return (
            f"{how} {n} real professional candidates matching this recruiter search: "
            f'"{query}"{loc}.\n'
            f"{'Run several web searches with varied keywords and sites' if web else 'Draw on well-known engineers, open-source contributors and public figures'} "
            f"to reach {n} DISTINCT real people (include strong AND reasonable matches). For EACH person, "
            "on its own numbered line, give:\n"
            "full name — current role/headline — company — location — public profile URL "
            "(a real GitHub / LinkedIn / portfolio link) — one short reason they fit."
            f"{avoid}"
        )

    def _to_json(self, listing: str) -> list[dict[str, Any]]:
        """Second step: turn the freeform candidate list into strict JSON (no tools, fast)."""
        prompt = (
            "Convert the candidate list below into a JSON array. Output ONLY the array. Each object: "
            '{"full_name","headline","location","company","skills":[...],"profile_url","email"(or null),'
            '"ai_summary"}. Skip any entry without a real profile URL.\n\nLIST:\n' + listing
        )
        return _extract_json_array(self._call(prompt, system=_SYSTEM_JSON, use_web_search=False))

    def _round(
        self, n: int, query: str, location: str | None, exclude: list[str], *, web: bool = True
    ) -> list[dict[str, Any]]:
        listing = self._call(
            self._search_prompt(n, query, location, exclude, web=web),
            system=_SYSTEM_SEARCH,
            use_web_search=web,
        )
        if not listing.strip():
            return []
        return [p for it in self._to_json(listing) if (p := _to_profile(it, location, ai_sourced=True))]

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.anthropic_api_key:
            logger.warning("ANTHROPIC_API_KEY not set — Claude sourcing disabled.")
            return []
        # We return one batch (page 1). Avoid duplicate fan-out on later pages.
        if page and page > 1:
            return []

        target = min(max(page_size or _MIN_TARGET, _MIN_TARGET), _MAX_RESULTS)

        # Two-step per round (web-search list -> JSON), with a top-up round if the first comes up
        # short. Splitting search from formatting keeps the JSON reliable; the top-up smooths the
        # variance in how many people one search surfaces.
        collected: dict[str, dict[str, Any]] = {}
        for rnd in range(_MAX_ROUNDS):
            need = max(target - len(collected), 5)
            try:
                rows = self._round(need, query, location, [p["full_name"] for p in collected.values()])
            except Exception as exc:
                logger.warning(f"Claude sourcing round {rnd + 1} failed ({exc}).")
                continue  # a failed/empty round shouldn't end the search — try again
            added = 0
            for p in rows:
                key = p["profile_url"].rstrip("/").lower()
                if key not in collected:
                    collected[key] = p
                    added += 1
            logger.info(f"Claude sourcing round {rnd + 1}: +{added} (total {len(collected)}/{target})")
            if len(collected) >= target:
                break
            # Stop early on diminishing returns ONLY once we already have some results; an empty
            # collection means the round genuinely failed (web-search variance / parse) — retry.
            if added == 0 and collected:
                break

        # Last-resort fallback: if every web-search round came up empty (search unavailable or
        # flaky), answer from the model's own knowledge so the recruiter isn't left with nothing.
        if not collected:
            try:
                rows = self._round(target, query, location, [], web=False)
                for p in rows:
                    collected[p["profile_url"].rstrip("/").lower()] = p
                logger.info(f"Claude sourcing knowledge fallback: {len(collected)} result(s)")
            except Exception as exc:
                logger.warning(f"Claude sourcing knowledge fallback failed ({exc}).")

        return list(collected.values())
