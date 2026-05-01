import os
from typing import Any

import requests

from .scraper_base import BaseScraperProvider


class LevelsFyiProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__(platform_name="levelsfyi", site_domain="levels.fyi", result_pattern="levels.fyi")

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        role = query
        loc = location if location else ""

        queries = [f'site:levels.fyi/salaries "{role}"', f'site:levels.fyi/salaries "{role}" "{loc}"']

        profiles = []
        seen_urls = set()

        username = os.getenv("OXYLABS_USERNAME")
        password = os.getenv("OXYLABS_PASSWORD")

        if not username or not password:
            return super().search(query, location, page, page_size)

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

                            if url and "levels.fyi" in url:
                                url = url.split("?")[0]
                                if url in seen_urls:
                                    continue
                                seen_urls.add(url)

                                name = title.split("–")[0].split("|")[0].split("-")[0].strip()
                                if "Levels.fyi" in name:
                                    name = name.replace("Levels.fyi", "").strip()
                                if not name:
                                    name = f"{role.title()} Compensation Data"

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
                                            "source": "oxylabs_levelsfyi",
                                            "title": title,
                                            "snippet": snippet,
                                        },
                                    }
                                )
            except Exception as e:
                print(f"DEBUG: Oxylabs Levels.fyi exception: {e}")

        if not profiles:
            return super().search(query, location, page, page_size)

        return profiles[:page_size]
