from typing import Any

from .scraper_base import BaseScraperProvider


class LevelsFyiProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__(platform_name="levelsfyi", site_domain="levels.fyi", result_pattern="levels.fyi")

    def _levelsfyi_name(self, title: str, role: str) -> str:
        name = title.split("–")[0].split("|")[0].split("-")[0].strip()
        if "Levels.fyi" in name:
            name = name.replace("Levels.fyi", "").strip()
        return name or f"{role.title()} Compensation Data"

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        role = query
        loc = location or ""
        queries = [f'site:levels.fyi/salaries "{role}"', f'site:levels.fyi/salaries "{role}" "{loc}"']

        profiles: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for q in queries:
            for item in self._oxylabs_organic(q, page):
                url = item.get("url")
                if not url or "levels.fyi" not in url:
                    continue
                url = url.split("?")[0]
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                title = item.get("title", "")
                profiles.append(
                    self._make_profile(
                        url=url,
                        title=title,
                        snippet=item.get("snippet", ""),
                        location=loc,
                        source="oxylabs_levelsfyi",
                        full_name=self._levelsfyi_name(title, role),
                    )
                )

        if not profiles:
            return super().search(query, location, page, page_size)
        return profiles[:page_size]
