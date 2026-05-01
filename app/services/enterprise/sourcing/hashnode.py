from typing import Any

import requests

from .base import SourcingProvider

HASHNODE_GQL_URL = "https://gql.hashnode.com"


class HashnodeProvider(SourcingProvider):
    @property
    def platform_name(self) -> str:
        return "hashnode"

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        """
        Two strategies combined:
        1. searchUsers(searchTerm) — direct user name/handle search
        2. tag posts — find authors who write about this topic/skill
        Both are deduplicated and merged.
        """
        profiles: list[dict[str, Any]] = []
        seen: set = set()

        # Strategy 1: search users by name/keyword
        user_results = self._search_users(query, page, page_size)
        for p in user_results:
            key = p["profile_url"]
            if key not in seen:
                seen.add(key)
                profiles.append(p)

        # Strategy 2: tag-based author discovery (great for skill keywords like "react")
        if len(profiles) < page_size:
            tag_results = self._search_by_tag(query, location, page, page_size)
            for p in tag_results:
                key = p["profile_url"]
                if key not in seen:
                    seen.add(key)
                    profiles.append(p)

        print(f"DEBUG: Hashnode total unique profiles: {len(profiles)}")
        return profiles[:page_size]

    # ──────────────────────────────────────────────────────────────────────────
    # Strategy 1: searchUsers — search by name / handle
    # Schema: searchUsers(searchTerm: String!, page: Int!, pageSize: Int!)
    #         returns SearchUserConnection { nodes { id user { ... } } }
    # ──────────────────────────────────────────────────────────────────────────
    def _search_users(self, query: str, page: int, page_size: int) -> list[dict[str, Any]]:
        gql = """
        query ($searchTerm: String!, $page: Int!, $pageSize: Int!) {
          searchUsers(searchTerm: $searchTerm, page: $page, pageSize: $pageSize) {
            nodes {
              user {
                username
                name
                tagline
                location
                profilePicture
                socialMediaLinks {
                  twitter
                  github
                  linkedin
                  website
                }
              }
            }
          }
        }
        """
        try:
            resp = requests.post(
                HASHNODE_GQL_URL,
                json={"query": gql, "variables": {"searchTerm": query, "page": page, "pageSize": page_size}},
                headers={"Content-Type": "application/json"},
                timeout=12,
            )
            print(f"DEBUG: Hashnode searchUsers status: {resp.status_code}")
            if resp.status_code != 200:
                return []

            data = resp.json()
            nodes = data.get("data", {}).get("searchUsers", {}).get("nodes", [])
            return [self._user_to_profile(n["user"]) for n in nodes if n.get("user")]

        except Exception as e:
            print(f"DEBUG: Hashnode searchUsers error: {e}")
            return []

    # ──────────────────────────────────────────────────────────────────────────
    # Strategy 2: tag posts — great for skill keywords
    # Schema: tag(slug: String!) { posts(first, filter: { sortBy: popular }) }
    # ──────────────────────────────────────────────────────────────────────────
    def _search_by_tag(
        self, query: str, location: str | None, page: int, page_size: int
    ) -> list[dict[str, Any]]:
        tag_slug = query.lower().strip().replace(" ", "-")

        gql = """
        query ($slug: String!, $first: Int!) {
          tag(slug: $slug) {
            name
            posts(first: $first, filter: { sortBy: popular }) {
              edges {
                node {
                  title
                  url
                  author {
                    username
                    name
                    tagline
                    location
                    profilePicture
                    socialMediaLinks {
                      twitter
                      github
                      linkedin
                      website
                    }
                  }
                }
              }
            }
          }
        }
        """
        try:
            resp = requests.post(
                HASHNODE_GQL_URL,
                json={"query": gql, "variables": {"slug": tag_slug, "first": page_size}},
                headers={"Content-Type": "application/json"},
                timeout=12,
            )
            print(f"DEBUG: Hashnode tag search status: {resp.status_code}")
            if resp.status_code != 200:
                return []

            data = resp.json()
            tag_data = data.get("data", {}).get("tag")
            if not tag_data:
                print(f"DEBUG: Hashnode tag '{tag_slug}' not found.")
                return []

            edges = tag_data.get("posts", {}).get("edges", [])
            profiles = []
            for edge in edges:
                author = edge.get("node", {}).get("author")
                if author:
                    profiles.append(self._user_to_profile(author))
            return profiles

        except Exception as e:
            print(f"DEBUG: Hashnode tag search error: {e}")
            return []

    # ──────────────────────────────────────────────────────────────────────────
    # Map a Hashnode User object → unified profile dict
    # ──────────────────────────────────────────────────────────────────────────
    def _user_to_profile(self, user: dict[str, Any]) -> dict[str, Any]:
        username = user.get("username", "")
        social = user.get("socialMediaLinks") or {}

        social_links = []
        if social.get("twitter"):
            social_links.append({"provider": "twitter", "url": social["twitter"]})
        if social.get("github"):
            social_links.append({"provider": "github", "url": social["github"]})
        if social.get("linkedin"):
            social_links.append({"provider": "linkedin", "url": social["linkedin"]})
        if social.get("website"):
            social_links.append({"provider": "website", "url": social["website"]})

        return {
            "full_name": user.get("name") or username,
            "headline": user.get("tagline") or "Writer on Hashnode",
            "location": user.get("location"),
            "platform": "hashnode",
            "profile_url": f"https://hashnode.com/@{username}",
            "email": None,
            "avatar_url": user.get("profilePicture"),
            "skills": [],
            "social_links": social_links,
            "raw_data": user,
        }
