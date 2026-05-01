import os
from typing import Any

import requests

from .scraper_base import BaseScraperProvider


class DribbbleProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__(platform_name="dribbble", site_domain="dribbble.com", result_pattern="dribbble.com/")

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        role = query
        loc = location if location else ""

        queries = [f"site:dribbble.com {role}", f"site:dribbble.com {role} {loc}"]

        profiles = []
        seen_urls = set()

        username = os.getenv("OXYLABS_USERNAME")
        password = os.getenv("OXYLABS_PASSWORD")

        if username and password:
            for q in queries:
                payload = {
                    "source": "google_search",
                    "query": q,
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

                        for res in results:
                            organic_results = res.get("content", {}).get("results", {}).get("organic", [])
                            if not organic_results:
                                organic_results = res.get("content", {}).get("organic", [])

                            for item in organic_results:
                                url = item.get("url")
                                title = item.get("title", "")
                                snippet = item.get("snippet", "")

                                # Profile URLs usually are dribbble.com/username
                                if (
                                    url
                                    and "dribbble.com/" in url
                                    and not any(
                                        k in url
                                        for k in [
                                            "/shots/",
                                            "/tags/",
                                            "/search",
                                            "/places",
                                            "/stories",
                                            "/about",
                                            "/pro",
                                        ]
                                    )
                                ):
                                    url = url.split("?")[0]
                                    if url in seen_urls:
                                        continue
                                    seen_urls.add(url)

                                    name = title.split("|")[0].split("-")[0].split("–")[0].strip()
                                    if "Dribbble" in name:
                                        name = name.replace("Dribbble", "").strip()

                                    if not name:
                                        name = url.split("/")[-1].replace("-", " ").title()

                                    profiles.append(
                                        {
                                            "full_name": name,
                                            "headline": snippet,
                                            "location": loc,
                                            "platform": self._platform_name,
                                            "profile_url": url,
                                            "skills": [],
                                            "social_links": [],
                                            "raw_data": {
                                                "source": "oxylabs_dribbble",
                                                "title": title,
                                                "snippet": snippet,
                                            },
                                        }
                                    )
                except Exception as e:
                    print(f"DEBUG: Oxylabs Dribbble exception: {e}")

        if not profiles:
            return super().search(query, location, page, page_size)

        return profiles[:page_size]
