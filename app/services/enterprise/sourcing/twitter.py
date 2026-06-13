import random
import re
import time
from typing import Any, ClassVar
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from app.core.settings import settings

from .scraper_base import BaseScraperProvider


class TwitterProvider(BaseScraperProvider):
    """
    Twitter/X profile search provider.

    Strategy
    --------
    PRIMARY  — twitterapi.io "Search user by keyword" (when TWITTERAPI_IO_KEY is set)
    FALLBACK — BeautifulSoup pipeline:
        1. Reuse the parent class _search_google / _search_duckduckgo / _search_bing
           methods (they use find_all('a') + regex — proven to work for all platforms).
        2. Filter the collected URLs down to valid single-segment twitter.com profile paths.
        3. Fetch each profile page individually and parse <meta og:…> tags with
           BeautifulSoup to get name, bio, and avatar without JavaScript.
    """

    TWITTERAPI_BASE = "https://api.twitterapi.io"

    EXCLUDE_SEGMENTS: ClassVar[set[str]] = {
        "search",
        "status",
        "hashtag",
        "explore",
        "i",
        "settings",
        "messages",
        "notifications",
        "home",
        "login",
        "signup",
        "tos",
        "privacy",
        "intent",
        "share",
        "oauth",
    }

    def __init__(self):
        super().__init__(platform_name="twitter", site_domain="twitter.com", result_pattern="twitter.com/")

    # ------------------------------------------------------------------ #
    #  Public entry point                                                   #
    # ------------------------------------------------------------------ #

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        api_key = settings.twitterapi_io_key

        if api_key:
            print(f"DEBUG: [Twitter] Using twitterapi.io for query: '{query}'")
            results = self._search_twitterapi_io(query, location, page, page_size, api_key)
            if results:
                return results
            print("DEBUG: [Twitter] twitterapi.io returned 0 — falling back to BS4 scraper.")

        print("DEBUG: [Twitter] BeautifulSoup scraper pipeline starting.")
        return self._search_with_bs4(query, location, page, page_size)

    # ------------------------------------------------------------------ #
    #  twitterapi.io path (primary when key is configured)                 #
    # ------------------------------------------------------------------ #

    def _search_twitterapi_io(self, query, location, page, page_size, api_key):
        url = f"{self.TWITTERAPI_BASE}/twitter/user/search"
        headers = {"X-API-Key": api_key}
        cursor = ""
        raw_users: list[dict] = []

        for _ in range(page):
            params: dict[str, Any] = {"query": query}
            if cursor:
                params["cursor"] = cursor
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=12)
                print(f"DEBUG: [Twitter-API] status={resp.status_code}")
                if resp.status_code != 200:
                    return []
                data = resp.json()
                if data.get("status") != "success":
                    return []
                raw_users = data.get("users", [])
                if not data.get("has_next_page"):
                    break
                cursor = data.get("next_cursor", "")
            except Exception as e:
                print(f"DEBUG: [Twitter-API] Exception: {e}")
                return []

        return self._map_api_users(raw_users[:page_size], location)

    def _map_api_users(self, users, location):
        profiles = []
        for user in users:
            username = user.get("userName") or ""
            name = user.get("name") or username
            description = user.get("description") or ""
            user_location = user.get("location") or location
            blog_url = None
            for u in user.get("profile_bio", {}).get("entities", {}).get("url", {}).get("urls", []):
                blog_url = u.get("expanded_url")
                break
            profile_url = f"https://twitter.com/{username}" if username else user.get("url", "")
            profiles.append(
                {
                    "full_name": name,
                    "headline": description,
                    "location": user_location,
                    "platform": "twitter",
                    "profile_url": profile_url,
                    "email": None,
                    "avatar_url": user.get("profilePicture"),
                    "company": None,
                    "blog": blog_url,
                    "twitter_username": username,
                    "public_repos": None,
                    "followers": user.get("followers"),
                    "following": user.get("following"),
                    "hireable": None,
                    "skills": [],
                    "social_links": [{"provider": "twitter", "url": profile_url}],
                    "raw_data": user,
                }
            )
        return profiles

    # ------------------------------------------------------------------ #
    #  BeautifulSoup scraper pipeline (fallback)                           #
    # ------------------------------------------------------------------ #

    def _search_with_bs4(
        self, query: str, location: str | None, page: int, page_size: int
    ) -> list[dict[str, Any]]:
        """
        Step 1: Use the PARENT class search-engine methods (proven to work) to
                collect raw profiles that contain twitter.com URLs.
        Step 2: Filter URLs to real single-segment profile paths.
        Step 3: Enrich each URL by fetching the twitter page and reading <meta> og: tags.
        """
        raw_profiles = self._collect_via_parent(query, location, page_size * 3)

        if not raw_profiles:
            print("DEBUG: [Twitter-BS4] Parent class found 0 candidates — trying x.com variant.")
            # Some pages link to x.com instead of twitter.com
            self.result_pattern = "x.com/"
            self.site_domain = "x.com"
            raw_profiles = self._collect_via_parent(query, location, page_size * 3)
            # Restore
            self.result_pattern = "twitter.com/"
            self.site_domain = "twitter.com"

        if not raw_profiles:
            print("DEBUG: [Twitter-BS4] No candidates from any engine.")
            return []

        # Deduplicate and validate URLs
        seen: set = set()
        valid_urls: list[str] = []
        for p in raw_profiles:
            url = p.get("profile_url", "")
            # Normalise x.com → twitter.com
            url = url.replace("x.com/", "twitter.com/").replace("//www.", "//")
            if url and url not in seen and self._is_profile_url(url):
                seen.add(url)
                valid_urls.append(url)

        print(f"DEBUG: [Twitter-BS4] {len(raw_profiles)} raw → {len(valid_urls)} valid profile URLs.")

        if not valid_urls:
            # Return the raw profiles as-is (at least they have the URL)
            return raw_profiles[:page_size]

        # Enrich each URL with meta-tag data
        enriched: list[dict[str, Any]] = []
        for url in valid_urls[:page_size]:
            profile = self._enrich_from_meta(url, location)
            if profile:
                enriched.append(profile)
            time.sleep(0.25)  # polite delay

        return enriched

    def _collect_via_parent(self, query: str, location: str | None, limit: int) -> list[dict[str, Any]]:
        """
        Call the working base-class methods directly with twitter-specific
        query variations. These methods use find_all('a') + regex and are
        proven to work across search engines.
        """
        profiles: list[dict] = []
        query_variations = [
            f"site:twitter.com {query}",
            f'"twitter.com" "{query}"',
            f"twitter.com {query} developer",
        ]

        import os

        username = os.getenv("OXYLABS_USERNAME")
        password = os.getenv("OXYLABS_PASSWORD")

        if username and password:
            print("DEBUG: [Twitter-BS4] Attempting Oxylabs Google search fallback.")
            for q_var in query_variations:
                payload = {
                    "source": "google_search",
                    "query": q_var,
                    "geo_location": "United States",
                    "parse": True,
                    "start_page": 1,
                    "pages": 1,
                }
                try:
                    response = requests.post(
                        "https://realtime.oxylabs.io/v1/queries",
                        auth=(username, password),
                        json=payload,
                        timeout=20,
                    )
                    if response.status_code == 200:
                        data = response.json()
                        results = data.get("results", [])
                        for res in results:
                            organic_results = res.get("content", {}).get("results", {}).get(
                                "organic", []
                            ) or res.get("content", {}).get("organic", [])
                            for item in organic_results:
                                url = item.get("url")
                                title = item.get("title", "")
                                snippet = item.get("snippet", "")
                                if url and ("twitter.com/" in url or "x.com/" in url):
                                    profiles.append(
                                        {
                                            "full_name": title.split("–")[0]
                                            .split("|")[0]
                                            .split("-")[0]
                                            .strip(),
                                            "headline": snippet,
                                            "location": location,
                                            "platform": "twitter",
                                            "profile_url": url.split("?")[0],
                                            "skills": [],
                                            "social_links": [],
                                        }
                                    )
                except Exception as e:
                    print(f"DEBUG: [Twitter-Oxylabs] Exception: {e}")
            if profiles:
                return profiles

        for q_var in query_variations:
            print(f"DEBUG: [Twitter-BS4] Trying query: {q_var}")

            # DuckDuckGo
            try:
                found = self._search_duckduckgo(q_var, location, limit)
                print(f"DEBUG: [Twitter-BS4] DDG → {len(found)} results")
                profiles.extend(found)
            except Exception as e:
                print(f"DEBUG: [Twitter-BS4] DDG error: {e}")

            # Bing
            try:
                found = self._search_bing(q_var, location, limit)
                print(f"DEBUG: [Twitter-BS4] Bing → {len(found)} results")
                profiles.extend(found)
            except Exception as e:
                print(f"DEBUG: [Twitter-BS4] Bing error: {e}")

            # Google
            try:
                found = self._search_google(q_var, location, limit)
                print(f"DEBUG: [Twitter-BS4] Google → {len(found)} results")
                profiles.extend(found)
            except Exception as e:
                print(f"DEBUG: [Twitter-BS4] Google error: {e}")

            if len(profiles) >= limit:
                break

        return profiles

    # ------------------------------------------------------------------ #
    #  Meta-tag enrichment                                                 #
    # ------------------------------------------------------------------ #

    def _enrich_from_meta(self, profile_url: str, location: str | None) -> dict[str, Any] | None:
        """
        Fetch the Twitter profile page (static HTML) and extract data from:
            <meta property="og:title">       → name (@username)
            <meta property="og:description"> → bio
            <meta property="og:image">       → avatar
        These tags are rendered in static HTML even without JavaScript.
        """
        username = self._extract_username(profile_url)

        try:
            ua = random.choice(self.user_agents)
            resp = requests.get(
                profile_url,
                headers={
                    "User-Agent": ua,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                },
                timeout=8,
                allow_redirects=True,
            )

            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")

                def meta(prop: str, attr: str = "property") -> str:
                    tag = soup.find("meta", {attr: prop})
                    return (tag.get("content") or "").strip() if tag else ""

                og_title = meta("og:title") or meta("twitter:title", "name")
                og_desc = meta("og:description") or meta("twitter:description", "name")
                og_image = meta("og:image") or meta("twitter:image:src", "name")

                # og:title format: "Name (@username) / X" or "Name (@username) on Twitter"
                name = None
                if og_title:
                    name = og_title.split("(")[0].strip()
                    name = re.sub(r"\s*/\s*X$", "", name).strip()
                    name = re.sub(r"\s+on Twitter$", "", name).strip()

                bio = og_desc.strip() if og_desc else None
                # Strip Twitter's generic suffix from bio
                if bio:
                    bio = re.sub(r"\s*\.\s*See their Twitter profile\.*$", "", bio).strip()

                if name or bio or og_image:
                    print(f"DEBUG: [Twitter-BS4] Meta enriched @{username}: '{name}'")
                    return self._build_profile(
                        username=username or "",
                        name=name or username or "Twitter User",
                        bio=bio,
                        avatar=og_image or None,
                        location=location,
                        profile_url=f"https://twitter.com/{username}" if username else profile_url,
                        source="bs4_meta",
                    )
            else:
                print(f"DEBUG: [Twitter-BS4] HTTP {resp.status_code} for {profile_url}")

        except Exception as e:
            print(f"DEBUG: [Twitter-BS4] Fetch error for {profile_url}: {e}")

        # Fallback: create a minimal profile from URL alone
        if username:
            print(f"DEBUG: [Twitter-BS4] URL-only fallback for @{username}")
            return self._build_profile(
                username=username,
                name=username,
                bio=None,
                avatar=None,
                location=location,
                profile_url=f"https://twitter.com/{username}",
                source="bs4_url_only",
            )
        return None

    def _build_profile(self, username, name, bio, avatar, location, profile_url, source) -> dict[str, Any]:
        return {
            "full_name": name,
            "headline": bio,
            "location": location,
            "platform": "twitter",
            "profile_url": profile_url,
            "email": None,
            "avatar_url": avatar,
            "company": None,
            "blog": None,
            "twitter_username": username,
            "public_repos": None,
            "followers": None,
            "following": None,
            "hireable": None,
            "skills": [],
            "social_links": [{"provider": "twitter", "url": profile_url}],
            "raw_data": {"source": source, "profile_url": profile_url},
        }

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _is_profile_url(self, url: str) -> bool:
        """True only for single-segment twitter.com paths like /elonmusk."""
        try:
            parsed = urlparse(url)
            netloc = parsed.netloc.replace("www.", "")
            if netloc not in ("twitter.com", "x.com"):
                return False
            segments = [s for s in parsed.path.strip("/").split("/") if s]
            if len(segments) != 1:
                return False
            if segments[0].lower() in self.EXCLUDE_SEGMENTS:
                return False
            # Twitter usernames are alphanumeric + underscore, 1-15 chars
            return re.match(r"^[A-Za-z0-9_]{1,50}$", segments[0])
        except Exception:
            return False

    def _extract_username(self, url: str) -> str | None:
        try:
            parts = [s for s in urlparse(url).path.strip("/").split("/") if s]
            return parts[0].lower() if parts else None
        except Exception:
            return None
