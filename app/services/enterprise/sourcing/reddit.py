from typing import Any

import requests

from .base import SourcingProvider


class RedditProvider(SourcingProvider):
    @property
    def platform_name(self) -> str:
        return "reddit"

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        # Reddit User Search API (Public JSON)
        url = "https://www.reddit.com/users/search.json"
        params = {"q": query, "limit": page_size, "type": "user"}
        headers = {"User-Agent": "Talent-Intel-Bot/1.0"}

        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            if response.status_code != 200:
                return []

            data = response.json()
            items = data.get("data", {}).get("children", [])

            profiles = []
            for item in items:
                user_data = item.get("data", {})
                username = user_data.get("name")

                profiles.append(
                    {
                        "full_name": f"u/{username}",
                        "headline": user_data.get("public_description", ""),
                        "location": None,
                        "platform": "reddit",
                        "profile_url": f"https://www.reddit.com/user/{username}",
                        "email": None,
                        "avatar_url": user_data.get("icon_img"),
                        "skills": [],
                        "social_links": [],
                        "raw_data": user_data,
                    }
                )

            return profiles
        except Exception as e:
            print(f"DEBUG: Reddit API error: {e}")
            return []
