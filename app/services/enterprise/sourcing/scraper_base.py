import random
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from .base import SourcingProvider


class BaseScraperProvider(SourcingProvider):
    def __init__(self, platform_name: str, site_domain: str, result_pattern: str):
        self._platform_name = platform_name
        self.site_domain = site_domain
        self.result_pattern = result_pattern
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        ]

    @property
    def platform_name(self) -> str:
        return self._platform_name

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        import os

        username = os.getenv("OXYLABS_USERNAME")
        password = os.getenv("OXYLABS_PASSWORD")

        profiles = []
        query_variations = [
            f"site:{self.site_domain} {query}",
            f"{self.site_domain} {query}",
            f'"{self.site_domain}" {query}',
        ]

        if username and password:
            print(f"DEBUG: Using Oxylabs for BaseScraperProvider search ({self._platform_name})")
            for q_var in query_variations:
                payload = {
                    "source": "google_search",
                    "query": q_var,
                    "geo_location": "United States",
                    "parse": True,
                    "start_page": page,
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
                        seen_urls = set()

                        for res in results:
                            organic_results = res.get("content", {}).get("results", {}).get("organic", [])
                            if not organic_results:
                                organic_results = res.get("content", {}).get("organic", [])

                            for item in organic_results:
                                url = item.get("url")
                                title = item.get("title", "")
                                snippet = item.get("snippet", "")

                                if url and self.result_pattern in url:
                                    url = url.split("?")[0]
                                    if url in seen_urls:
                                        continue
                                    seen_urls.add(url)

                                    name = title.split("–")[0].split("|")[0].split("-")[0].strip()
                                    if not name:
                                        name = url.split("/")[-1].replace("-", " ").title()

                                    profiles.append(
                                        {
                                            "full_name": name,
                                            "headline": snippet,
                                            "location": location,
                                            "platform": self._platform_name,
                                            "profile_url": url,
                                            "skills": [],
                                            "social_links": [],
                                            "raw_data": {
                                                "source": "oxylabs_base_fallback",
                                                "title": title,
                                                "snippet": snippet,
                                            },
                                        }
                                    )
                        if profiles:
                            return profiles[:page_size]
                except Exception as e:
                    print(f"DEBUG: Oxylabs BaseScraper fallback exception: {e}")

        for q_var in query_variations:
            # Try Google
            try:
                print(f"DEBUG: Attempting Google search for {self._platform_name} with: {q_var}")
                profiles = self._search_google(q_var, location, page_size)
                if profiles:
                    break
            except Exception as e:
                print(f"DEBUG: Google search failed: {e}")

            # Try DuckDuckGo Lite
            try:
                print(f"DEBUG: Falling back to DuckDuckGo Lite for {self._platform_name} with: {q_var}")
                profiles = self._search_duckduckgo(q_var, location, page_size)
                if profiles:
                    break
            except Exception as e:
                print(f"DEBUG: DuckDuckGo search failed: {e}")

            # Try Bing
            try:
                print(f"DEBUG: Attempting Bing search for {self._platform_name} with: {q_var}")
                profiles = self._search_bing(q_var, location, page_size)
                if profiles:
                    break
            except Exception as e:
                print(f"DEBUG: Bing search failed: {e}")

        if not profiles:
            print(f"DEBUG: No profiles found for {self._platform_name} after trying all variations.")

        return profiles[:page_size]

    def _oxylabs_organic(self, query: str, page: int) -> list[dict[str, Any]]:
        """Run one Oxylabs google_search query and return its organic result items.

        Shared helper so individual providers don't each re-implement the request +
        response parsing. Returns ``[]`` when Oxylabs creds are missing or the call
        fails (callers then fall back to their own search path).
        """
        import os

        username = os.getenv("OXYLABS_USERNAME")
        password = os.getenv("OXYLABS_PASSWORD")
        if not username or not password:
            return []
        payload = {
            "source": "google_search",
            "query": query,
            "geo_location": "United States",
            "parse": True,
            "start_page": page,
            "pages": 1,
        }
        try:
            response = requests.post(
                "https://realtime.oxylabs.io/v1/queries", auth=(username, password), json=payload, timeout=20
            )
            if response.status_code != 200:
                return []
            items: list[dict[str, Any]] = []
            for res in response.json().get("results", []):
                content = res.get("content", {})
                organic = content.get("results", {}).get("organic", []) or content.get("organic", [])
                items.extend(organic)
            return items
        except Exception as e:  # pragma: no cover - network/parse best-effort
            print(f"DEBUG: Oxylabs exception ({self._platform_name}): {e}")
            return []

    def _make_profile(
        self,
        *,
        url: str,
        title: str,
        snippet: str,
        location: str | None,
        source: str,
        full_name: str | None = None,
    ) -> dict[str, Any]:
        """Build the standard candidate-profile dict every provider returns."""
        if not full_name:
            full_name = title.split("–")[0].split("|")[0].split("-")[0].strip()
            if not full_name:
                full_name = url.split("/")[-1].replace("-", " ").title()
        return {
            "full_name": full_name,
            "headline": snippet,
            "location": location,
            "platform": self._platform_name,
            "profile_url": url,
            "skills": [],
            "social_links": [],
            "raw_data": {"source": source, "title": title, "snippet": snippet},
        }

    def _search_google(self, full_query: str, location: str | None, page_size: int) -> list[dict[str, Any]]:
        url = "https://www.google.com/search"
        headers = {
            "User-Agent": random.choice(self.user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        params = {"q": full_query, "num": page_size * 2}

        response = requests.get(url, headers=headers, params=params, timeout=10)
        profiles = []

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")

            # BROAD SEARCH: Find all links and filter by pattern
            all_links = soup.find_all("a")
            print(f"DEBUG: Google raw page has {len(all_links)} links")

            # ULTIMATE FALLBACK: If BS4 finds no links, use regex on raw text
            if not all_links:
                hrefs = re.findall(r'href="([^"]+)"', response.text)
                all_links = [{"href": h} for h in hrefs]

            for link_el in all_links:
                href = link_el.get("href", "")
                if not href:
                    continue

                # Unwrap Google redirect links
                if "/url?q=" in href:
                    href = parse_qs(urlparse(href).query).get("q", [href])[0]

                # Ensure it's a real profile link and not a Google internal link
                if self.result_pattern in href and "google.com" not in href and not href.startswith("/"):
                    # Found a candidate link! Now find a title and snippet
                    # Look up the DOM tree for a container
                    _parent = link_el.parent
                    title = "Professional Profile"
                    snippet = ""

                    # Heuristic: find h3 in parent or nearby
                    # First check the link itself
                    if link_el.find("h3"):
                        title = link_el.find("h3").get_text()
                    else:
                        # Try finding h3 in ancestors
                        current = link_el
                        for _ in range(5):  # Limit depth
                            if current.parent:
                                current = current.parent
                                h3 = current.find("h3")
                                if h3:
                                    title = h3.get_text()
                                    break

                    # Heuristic: find snippet (usually a div/span with lots of text)
                    current = link_el
                    for _ in range(3):
                        if current.parent:
                            current = current.parent
                            # Find all text-heavy elements in this container
                            for el in current.find_all(["div", "span"]):
                                text = el.get_text().strip()
                                if len(text) > 50 and text != title:
                                    snippet = text
                                    break
                            if snippet:
                                break

                    if not title or title == "Professional Profile":
                        # Heuristic: extract name from URL
                        url_path = urlparse(href).path
                        if url_path:
                            parts = [p for p in url_path.split("/") if p]
                            if parts:
                                title = parts[-1].replace("-", " ").title()

                    name = title.split("-")[0].split("|")[0].split("...")[0].strip()
                    email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", snippet)
                    email = email_match.group(0) if email_match else None

                    if not any(p["profile_url"] == href for p in profiles):
                        profiles.append(
                            {
                                "full_name": name,
                                "headline": snippet,
                                "location": location,
                                "platform": self._platform_name,
                                "profile_url": href,
                                "email": email,
                                "skills": [],
                                "social_links": [],
                                "raw_data": {"source": "google_broad", "title": title, "snippet": snippet},
                            }
                        )
        return profiles

    def _search_duckduckgo(
        self, full_query: str, location: str | None, page_size: int
    ) -> list[dict[str, Any]]:
        url = "https://duckduckgo.com/lite/"
        headers = {"User-Agent": random.choice(self.user_agents)}
        response = requests.get(url, params={"q": full_query}, headers=headers, timeout=10)
        profiles = []

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            rows = soup.find_all("tr")
            for row in rows:
                link_el = row.find("a", class_="result-link")
                if link_el:
                    href = link_el.get("href", "")
                    if self.result_pattern in href:
                        title = link_el.get_text()
                        name = title.split("(")[0].split("-")[0].strip()
                        snippet_row = row.find_next_sibling("tr")
                        snippet = snippet_row.get_text().strip() if snippet_row else ""

                        email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", snippet)
                        email = email_match.group(0) if email_match else None

                        if not any(p["profile_url"] == href for p in profiles):
                            profiles.append(
                                {
                                    "full_name": name,
                                    "headline": snippet,
                                    "location": location,
                                    "platform": self._platform_name,
                                    "profile_url": href,
                                    "email": email,
                                    "skills": [],
                                    "social_links": [],
                                    "raw_data": {
                                        "source": "duckduckgo_lite",
                                        "title": title,
                                        "snippet": snippet,
                                    },
                                }
                            )
        return profiles

    def _search_bing(self, full_query: str, location: str | None, page_size: int) -> list[dict[str, Any]]:
        url = "https://www.bing.com/search"
        headers = {"User-Agent": random.choice(self.user_agents)}
        params = {"q": full_query}

        response = requests.get(url, headers=headers, params=params, timeout=10)
        profiles = []

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            # Bing uses h2 for result titles
            for link_el in soup.find_all("a"):
                href = link_el.get("href", "")
                if self.result_pattern in href and "bing.com" not in href and "microsoft.com" not in href:
                    title = link_el.get_text()
                    _parent = link_el.parent
                    snippet = ""
                    # Look for snippet in parent's siblings
                    current = link_el
                    for _ in range(3):
                        if current.parent:
                            current = current.parent
                            p = current.find("p")
                            if p:
                                snippet = p.get_text()
                                break

                    name = title.split("-")[0].split("|")[0].strip()
                    if not any(p["profile_url"] == href for p in profiles):
                        profiles.append(
                            {
                                "full_name": name,
                                "headline": snippet,
                                "location": location,
                                "platform": self._platform_name,
                                "profile_url": href,
                                "email": None,
                                "skills": [],
                                "social_links": [],
                                "raw_data": {"source": "bing", "title": title, "snippet": snippet},
                            }
                        )
        return profiles
