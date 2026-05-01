import os
import re
from typing import Any
from urllib.parse import urlparse

import requests

from .scraper_base import BaseScraperProvider


class LinkedInProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__(
            platform_name="linkedin", site_domain="linkedin.com/in/", result_pattern="linkedin.com/in/"
        )

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        role = query
        loc = location if location else ""

        # Generate multiple queries to improve results
        queries = [
            f'site:linkedin.com/in "{role}" "{loc}"',
            f'site:linkedin.com/in "{role}" "{loc}" "experience"',
            f'site:linkedin.com/in intitle:"{role}" "{loc}"',
        ]

        profiles = []
        seen_urls = set()

        username = os.getenv("OXYLABS_USERNAME")
        password = os.getenv("OXYLABS_PASSWORD")

        if not username or not password:
            print("DEBUG: Oxylabs credentials missing. Falling back to BaseScraperProvider's search method.")
            return super().search(query, location, page, page_size)

        for q in queries:
            payload = {
                "source": "google_search",
                "query": q,
                "geo_location": "India" if loc.lower() == "india" else "United States",
                "parse": True,
                "start_page": page,
                "pages": 2,
            }

            try:
                print(f"DEBUG: Requesting Oxylabs for query: {q} with start_page={page}")
                response = requests.request(
                    "POST",
                    "https://realtime.oxylabs.io/v1/queries",
                    auth=(username, password),
                    json=payload,
                    timeout=20,
                )

                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])

                    for res in results:
                        organic_results = res.get("content", {}).get("results", {}).get("organic", [])
                        if not organic_results:
                            organic_results = res.get("content", {}).get("organic", [])

                        for item in organic_results:
                            url = item.get("url")
                            title = item.get("title", "")
                            snippet = item.get("snippet", "")

                            if url and "linkedin.com/in/" in url:
                                url = url.split("?")[0]

                                if url in seen_urls:
                                    continue
                                seen_urls.add(url)

                                name = title.split("-")[0].split("|")[0].split("...")[0].strip()
                                if not name or name.lower() == "linkedin":
                                    url_path = urlparse(url).path
                                    if url_path:
                                        parts = [p for p in url_path.split("/") if p]
                                        if parts:
                                            name = parts[-1].replace("-", " ").title()

                                email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", snippet)
                                email = email_match.group(0) if email_match else None

                                profiles.append(
                                    {
                                        "full_name": name,
                                        "headline": snippet,
                                        "location": loc,
                                        "platform": self._platform_name,
                                        "profile_url": url,
                                        "email": email,
                                        "skills": [],
                                        "social_links": [],
                                        "raw_data": {
                                            "source": "oxylabs_google",
                                            "title": title,
                                            "snippet": snippet,
                                        },
                                    }
                                )
                else:
                    print(f"DEBUG: Oxylabs API error: {response.status_code} - {response.text}")

            except Exception as e:
                print(f"DEBUG: Oxylabs API exception for query {q}: {e}")

        if not profiles:
            print("DEBUG: Oxylabs returned no profiles. Falling back to BaseScraperProvider's search method.")
            return super().search(query, location, page, page_size)

        # Enrich profiles with contact info via parallel Oxylabs searches
        from concurrent.futures import ThreadPoolExecutor

        def enrich_contact_info(profile):
            if profile.get("email"):
                return profile

            full_name = profile["full_name"]
            if not full_name or full_name == "Professional Profile":
                return profile

            enrich_query = f'"{full_name}" (email OR "@gmail.com" OR "contact")'
            payload = {
                "source": "google_search",
                "query": enrich_query,
                "geo_location": "India" if loc.lower() == "india" else "United States",
                "parse": True,
            }

            try:
                response = requests.request(
                    "POST",
                    "https://realtime.oxylabs.io/v1/queries",
                    auth=(username, password),
                    json=payload,
                    timeout=10,
                )
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    for res in results:
                        organic_results = res.get("content", {}).get("results", {}).get("organic", [])
                        if not organic_results:
                            organic_results = res.get("content", {}).get("organic", [])

                        for item in organic_results:
                            snippet = item.get("snippet", "")

                            # Email Regex
                            email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", snippet)
                            if email_match:
                                profile["email"] = email_match.group(0)
                                profile["raw_data"]["contact_source"] = "oxylabs_enrichment"

                            # Phone Regex
                            phone_match = re.search(
                                r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", snippet
                            )
                            if phone_match:
                                profile["raw_data"]["phone"] = phone_match.group(0)

                            if profile.get("email"):
                                return profile

            except Exception as e:
                print(f"DEBUG: Contact enrichment failed for {full_name}: {e}")

            return profile

        try:
            with ThreadPoolExecutor(max_workers=5) as executor:
                profiles_to_enrich = profiles[:page_size]
                enriched_profiles = list(executor.map(enrich_contact_info, profiles_to_enrich))
                profiles = enriched_profiles + profiles[page_size:]
        except Exception as e:
            print(f"DEBUG: Enrichment thread pool exception: {e}")

        # Scoring logic
        def score_profile(profile):
            score = 0
            snippet_lower = profile["headline"].lower() if profile["headline"] else ""
            name_lower = profile["full_name"].lower() if profile["full_name"] else ""

            if role.lower() in snippet_lower:
                score += 3
            if role.lower() in name_lower:
                score += 2
            if loc.lower() and loc.lower() in snippet_lower:
                score += 3

            return score

        profiles.sort(key=score_profile, reverse=True)

        return profiles[:page_size]
