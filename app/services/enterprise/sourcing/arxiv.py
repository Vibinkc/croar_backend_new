import xml.etree.ElementTree as ET
from typing import Any

import requests

from .base import SourcingProvider


def _text(parent: ET.Element, path: str, ns: dict[str, str]) -> str:
    """Text of a child element, or "" when the tag is missing or empty.

    ElementTree.find() returns None for a missing tag and .text is None for an empty one,
    so reaching straight through either raised AttributeError. That was caught by the broad
    except below and turned one malformed entry into "no results at all" for the whole feed.
    """
    node = parent.find(path, ns)
    return (node.text or "").strip() if node is not None else ""


class ArXivProvider(SourcingProvider):
    @property
    def platform_name(self) -> str:
        return "arxiv"

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        # ArXiv API uses an XML atom feed
        start = (page - 1) * page_size
        url = f"https://export.arxiv.org/api/query?search_query=all:{query}&start={start}&max_results={page_size}"

        try:
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                return []

            root = ET.fromstring(response.content)
            # Namespace for Atom feed. The http:// below is an XML namespace identifier,
            # not a network endpoint: it is matched literally against the feed, so switching
            # it to https would break parsing outright.
            ns = {"atom": "http://www.w3.org/2005/Atom"}  # NOSONAR

            profiles = []
            for entry in root.findall("atom:entry", ns):
                title = _text(entry, "atom:title", ns)
                summary = _text(entry, "atom:summary", ns)
                link = _text(entry, "atom:id", ns)

                # ArXiv entries are papers, so we extract authors as "profiles"
                authors = entry.findall("atom:author", ns)
                for author in authors:
                    author_name = _text(author, "atom:name", ns)
                    if not author_name:
                        continue

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
                                    "summary": (summary[:200] + "...") if len(summary) > 200 else summary,
                                    "arxiv_id": link,
                                },
                            }
                        )

            return profiles
        except Exception as e:
            print(f"DEBUG: ArXiv provider error: {e}")
            return []
