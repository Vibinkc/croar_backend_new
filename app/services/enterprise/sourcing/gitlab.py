import os
from typing import Any

import requests

from .scraper_base import BaseScraperProvider


class GitLabProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__(platform_name="gitlab", site_domain="gitlab.com", result_pattern="gitlab.com/")

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        role = query
        loc = location if location else ""

        queries = [f'site:gitlab.com "{role}"', f'site:gitlab.com "{role}" "{loc}"']

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

                                if (
                                    url
                                    and "gitlab.com/" in url
                                    and not any(k in url for k in ["/help/", "/explore/", "/docs/"])
                                ):
                                    url = url.split("?")[0]
                                    if url in seen_urls:
                                        continue
                                    seen_urls.add(url)

                                    name = title.split("–")[0].split("|")[0].split("-")[0].strip()
                                    if "GitLab" in name:
                                        name = name.replace("GitLab", "").strip()
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
                                                "source": "oxylabs_gitlab",
                                                "title": title,
                                                "snippet": snippet,
                                            },
                                        }
                                    )
                except Exception as e:
                    print(f"DEBUG: Oxylabs GitLab exception: {e}")

        # Fallback to standard GitLab API
        if not profiles:
            url = "https://gitlab.com/api/v4/users"
            params = {"search": query, "per_page": page_size, "page": page}
            try:
                response = requests.get(url, params=params, timeout=10)
                if response.status_code == 200:
                    items = response.json()
                    for item in items:
                        _user_id = item.get("id")
                        username = item.get("username")

                        profiles.append(
                            {
                                "full_name": item.get("name"),
                                "headline": item.get("bio") or f"@{username}",
                                "location": item.get("location"),
                                "platform": "gitlab",
                                "profile_url": item.get("web_url"),
                                "email": item.get("public_email") or item.get("email"),
                                "avatar_url": item.get("avatar_url"),
                                "company": item.get("organization"),
                                "blog": item.get("website_url"),
                                "twitter_username": item.get("twitter"),
                                "skills": [],
                                "social_links": [],
                                "raw_data": item,
                            }
                        )
            except Exception as e:
                print(f"DEBUG: GitLab fallback REST exception: {e}")

        if not profiles:
            return super().search(query, location, page, page_size)

        return profiles[:page_size]
