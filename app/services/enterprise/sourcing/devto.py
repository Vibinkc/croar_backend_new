from typing import Any

import requests

from .base import SourcingProvider


class DevToProvider(SourcingProvider):
    @property
    def platform_name(self) -> str:
        return "devto"

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        # Forem API (Dev.to)
        # Search for articles and extract authors as profiles
        url = "https://dev.to/api/articles"
        params = {
            "tag": query,  # Use query as tag search
            "per_page": page_size,
            "page": page,
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                # If tag search fails, try query as keyword in search API
                url = "https://dev.to/api/articles"
                params = {"query": query, "per_page": page_size, "page": page}
                response = requests.get(url, params=params, timeout=10)

            if response.status_code != 200:
                return []

            items = response.json()
            profiles = []
            for item in items:
                user = item.get("user", {})
                username = user.get("username")

                if not any(p["profile_url"] == f"https://dev.to/{username}" for p in profiles):
                    profiles.append(
                        {
                            "full_name": user.get("name"),
                            "headline": f"Author on Dev.to | @{username}",
                            "location": None,
                            "platform": "devto",
                            "profile_url": f"https://dev.to/{username}",
                            "email": None,
                            "avatar_url": user.get("profile_image_90"),
                            "skills": item.get("tag_list", []),
                            "social_links": [],
                            "raw_data": item,
                        }
                    )

            return profiles
        except Exception as e:
            print(f"DEBUG: DevTo API error: {e}")
            return []
