import xml.etree.ElementTree as ET
from typing import Any

import requests

from .base import SourcingProvider


class ArXivProvider(SourcingProvider):
    @property
    def platform_name(self) -> str:
        return "arxiv"

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        # ArXiv API uses an XML atom feed
        start = (page - 1) * page_size
        url = f"http://export.arxiv.org/api/query?search_query=all:{query}&start={start}&max_results={page_size}"

        try:
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                return []

            root = ET.fromstring(response.content)
            # Namespace for Atom feed
            ns = {"atom": "http://www.w3.org/2005/Atom"}

            profiles = []
            for entry in root.findall("atom:entry", ns):
                title = entry.find("atom:title", ns).text.strip()
                summary = entry.find("atom:summary", ns).text.strip()
                link = entry.find("atom:id", ns).text.strip()

                # ArXiv entries are papers, so we extract authors as "profiles"
                authors = entry.findall("atom:author", ns)
                for author in authors:
                    author_name = author.find("atom:name", ns).text.strip()

                    if not any(p["full_name"] == author_name for p in profiles):
                        profiles.append(
                            {
                                "full_name": author_name,
                                "headline": f"Author of: {title}",
                                "location": None,
                                "platform": "arxiv",
                                "profile_url": f"https://arxiv.org/search/?query={author_name.replace(' ', '+')}&searchtype=author",
                                "email": None,
                                "skills": [],
                                "social_links": [],
                                "raw_data": {
                                    "last_paper": title,
                                    "summary": summary[:200] + "...",
                                    "arxiv_id": link,
                                },
                            }
                        )

            return profiles
        except Exception as e:
            print(f"DEBUG: ArXiv provider error: {e}")
            return []
