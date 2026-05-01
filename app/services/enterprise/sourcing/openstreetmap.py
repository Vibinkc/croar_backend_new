from typing import Any

import requests

from .base import SourcingProvider


class OpenStreetMapProvider(SourcingProvider):
    @property
    def platform_name(self) -> str:
        return "openstreetmap"

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        # Nominatim API for geo search
        url = "https://nominatim.openstreetmap.org/search"
        params = {"q": query, "format": "json", "limit": page_size}
        headers = {"User-Agent": "Talent-Intel-App/1.0"}

        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            if response.status_code != 200:
                return []

            items = response.json()
            profiles = []
            for item in items:
                profiles.append(
                    {
                        "full_name": item.get("display_name"),
                        "headline": f"Type: {item.get('type')} | Class: {item.get('class')}",
                        "location": item.get("display_name"),
                        "platform": "openstreetmap",
                        "profile_url": f"https://www.openstreetmap.org/#map=15/{item.get('lat')}/{item.get('lon')}",
                        "email": None,
                        "skills": [],
                        "social_links": [],
                        "raw_data": item,
                    }
                )

            return profiles
        except Exception as e:
            print(f"DEBUG: OpenStreetMap API error: {e}")
            return []
