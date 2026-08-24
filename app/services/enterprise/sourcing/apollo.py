"""
Apollo.io sourcing provider.

Uses the Apollo People API to source candidates and pull their full profile
(LinkedIn URL, title, company, employment history, location, photo) together with
contact details (email, and phone when available).

Requires APOLLO_API_KEY in the environment. Note: revealing emails/phones consumes
Apollo credits, so contact enrichment runs only on the page of results returned.

Docs: https://docs.apollo.io/reference/people-search
      https://docs.apollo.io/reference/people-enrichment
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from app.core.settings import get_settings

from .base import SourcingProvider

logger = logging.getLogger(__name__)

_BASE = "https://api.apollo.io"
_SEARCH_URL = f"{_BASE}/v1/mixed_people/search"
_MATCH_URL = f"{_BASE}/v1/people/match"
_TIMEOUT = 25


class ApolloProvider(SourcingProvider):
    """Source candidates from Apollo.io (LinkedIn-backed profile + contact data)."""

    @property
    def platform_name(self) -> str:
        return "apollo"

    def _headers(self) -> dict[str, str] | None:
        key = get_settings().apollo_api_key
        if not key:
            logger.warning("APOLLO_API_KEY is not set — Apollo sourcing is disabled.")
            return None
        return {"Content-Type": "application/json", "Cache-Control": "no-cache", "X-Api-Key": key}

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        headers = self._headers()
        if not headers:
            return []

        body: dict[str, Any] = {
            "q_keywords": query,
            "page": max(1, page),
            "per_page": max(1, min(page_size, 100)),
        }
        if location:
            body["person_locations"] = [location]

        try:
            resp = requests.post(_SEARCH_URL, headers=headers, json=body, timeout=_TIMEOUT)
            if resp.status_code != 200:
                logger.warning("Apollo search failed (%s): %s", resp.status_code, resp.text[:300])
                return []
            data = resp.json()
        except Exception:
            logger.exception("Apollo search request errored")
            return []

        people = data.get("people") or []
        profiles: list[dict[str, Any]] = []
        for person in people:
            enriched = self._enrich_contact(person, headers)
            profiles.append(self._to_profile(enriched or person))
        return profiles

    def _enrich_contact(self, person: dict[str, Any], headers: dict[str, str]) -> dict[str, Any] | None:
        """Reveal email / phone for a single person via the enrichment endpoint (costs credits)."""
        pid = person.get("id")
        if not pid:
            return None
        try:
            resp = requests.post(
                _MATCH_URL,
                headers=headers,
                json={"id": pid, "reveal_personal_emails": True},
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                return None
            return resp.json().get("person")
        except Exception:
            logger.debug("Apollo enrichment failed for %s", pid, exc_info=True)
            return None

    @staticmethod
    def _clean_email(email: str | None) -> str | None:
        # Apollo returns a masked placeholder when the email isn't unlocked.
        if not email or "email_not_unlocked" in email or email.split("@")[-1] == "domain.com":
            return None
        return email

    def _to_profile(self, person: dict[str, Any]) -> dict[str, Any]:
        org = person.get("organization") or {}
        name = person.get("name") or " ".join(
            filter(None, [person.get("first_name"), person.get("last_name")])
        )
        location = (
            ", ".join(filter(None, [person.get("city"), person.get("state"), person.get("country")])) or None
        )

        # Contact
        email = self._clean_email(person.get("email"))
        if not email:
            for e in person.get("personal_emails") or []:
                if self._clean_email(e):
                    email = e
                    break
        phones = [
            p.get("sanitized_number") or p.get("raw_number")
            for p in (person.get("phone_numbers") or [])
            if p.get("sanitized_number") or p.get("raw_number")
        ]

        # Social links (LinkedIn is the primary detail source).
        social_links: list[dict[str, str]] = []
        for key, label in (
            ("linkedin_url", "LinkedIn"),
            ("twitter_url", "Twitter"),
            ("github_url", "GitHub"),
            ("facebook_url", "Facebook"),
        ):
            if person.get(key):
                social_links.append({"type": label, "url": person[key]})

        # Skills / focus from Apollo's taxonomy.
        skills = list(
            dict.fromkeys(
                (person.get("keywords") or [])
                + (person.get("functions") or [])
                + (person.get("departments") or [])
            )
        )[:20]

        # Employment history (past LinkedIn roles) for context.
        history = [
            {
                "title": h.get("title"),
                "company": h.get("organization_name"),
                "start": h.get("start_date"),
                "end": h.get("end_date"),
                "current": h.get("current"),
            }
            for h in (person.get("employment_history") or [])
        ]

        return {
            "full_name": name or "Unknown",
            "headline": person.get("headline") or person.get("title"),
            "location": location,
            "platform": "apollo",
            "profile_url": person.get("linkedin_url") or "",
            "email": email,
            "avatar_url": person.get("photo_url"),
            "company": org.get("name") or person.get("organization_name"),
            "blog": org.get("website_url") or person.get("blog"),
            "twitter_username": (person.get("twitter_url") or "").rstrip("/").split("/")[-1] or None,
            "skills": skills,
            "social_links": social_links,
            "raw_data": {
                "apollo_id": person.get("id"),
                "title": person.get("title"),
                "seniority": person.get("seniority"),
                "phone_numbers": phones,
                "personal_emails": person.get("personal_emails") or [],
                "employment_history": history,
                "linkedin_url": person.get("linkedin_url"),
                "organization": {
                    "name": org.get("name"),
                    "industry": org.get("industry"),
                    "website": org.get("website_url"),
                    "linkedin": org.get("linkedin_url"),
                },
            },
        }
