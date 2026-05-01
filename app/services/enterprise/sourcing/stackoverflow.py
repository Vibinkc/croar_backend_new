from typing import Any

import requests

from .base import SourcingProvider


class StackOverflowProvider(SourcingProvider):
    @property
    def platform_name(self) -> str:
        return "stackoverflow"

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        # StackExchange API for users
        url = "https://api.stackexchange.com/2.3/users"
        params = {
            "order": "desc",
            "sort": "reputation",
            "inname": query,
            "site": "stackoverflow",
            "pagesize": page_size,
            "page": page,
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                return []

            data = response.json()
            items = data.get("items", [])

            profiles = []
            for item in items:
                profiles.append(
                    {
                        "full_name": item.get("display_name"),
                        "headline": f"Reputation: {item.get('reputation')} | {item.get('location', 'Global')}",
                        "location": item.get("location"),
                        "platform": "stackoverflow",
                        "profile_url": item.get("link"),
                        "email": None,
                        "avatar_url": item.get("profile_image"),
                        "skills": [],  # We could fetch top tags with another API call
                        "social_links": [],
                        "raw_data": item,
                    }
                )

            return profiles
        except Exception as e:
            print(f"DEBUG: StackOverflow API error: {e}")
            return []
