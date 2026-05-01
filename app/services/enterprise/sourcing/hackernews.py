from typing import Any

import requests

from .base import SourcingProvider


class HackerNewsProvider(SourcingProvider):
    @property
    def platform_name(self) -> str:
        return "hackernews"

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        """
        Improved HN Search:
        Instead of just searching user profiles (which are often empty),
        we search for comments and stories containing the keyword.
        Then we extract the authors who are actively discussing that topic.
        """
        url = "https://hn.algolia.com/api/v1/search"

        # We search comments (more likely to be individuals)
        # and stories (thought leaders/posters)
        params = {
            "query": query,
            "tags": "(comment,story)",
            "hitsPerPage": page_size * 2,  # Fetch more to allow for deduplication
            "page": page - 1,
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                print(f"DEBUG: HN Algolia returned {response.status_code}")
                return []

            data = response.json()
            hits = data.get("hits", [])

            profiles = []
            seen_authors = set()

            for hit in hits:
                author = hit.get("author")
                if not author or author in seen_authors:
                    continue

                seen_authors.add(author)

                # Create a headline based on what they were talking about
                title = hit.get("title") or hit.get("story_title")
                comment_text = hit.get("comment_text")

                headline = ""
                if title:
                    headline = f"Discussing: {title}"
                elif comment_text:
                    # Clean HTML tags from comment snippet
                    import re

                    clean_text = re.sub(r"<[^<]+?>", "", comment_text)[:100]
                    headline = f"Comment: {clean_text}..."

                profiles.append(
                    {
                        "full_name": author,
                        "headline": headline or "Active HN Contributor",
                        "location": None,
                        "platform": "hackernews",
                        "profile_url": f"https://news.ycombinator.com/user?id={author}",
                        "email": None,
                        "skills": [],
                        "social_links": [],
                        "raw_data": {
                            "hn_user": author,
                            "last_topic": title,
                            "snippet": comment_text[:200] if comment_text else None,
                            "points": hit.get("points"),
                            "created_at": hit.get("created_at"),
                        },
                    }
                )

                if len(profiles) >= page_size:
                    break

            print(f"DEBUG: HN found {len(profiles)} active contributors for '{query}'")
            return profiles

        except Exception as e:
            print(f"DEBUG: HackerNews API error: {e}")
            return []
