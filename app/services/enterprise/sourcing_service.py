import re
from typing import Any

from .sourcing.claude_ai import ClaudeSourcingProvider

# --- Legacy scraper providers (disabled) ---------------------------------------------
# The 27 platform scrapers below are replaced by Claude-powered sourcing (see
# ClaudeSourcingProvider). They are kept here, commented out, so they can be re-enabled
# quickly if needed — un-comment the imports + the matching entries in `self.providers`.
# from .sourcing.academicjournals import AcademicJournalsProvider
# from .sourcing.arxiv import ArXivProvider
# from .sourcing.behance import BehanceProvider
# from .sourcing.companywebsites import CompanyWebsitesProvider
# from .sourcing.conferencespeakers import ConferenceSpeakersProvider
# from .sourcing.crunchbase import CrunchbaseProvider
# from .sourcing.devto import DevToProvider
# from .sourcing.dribbble import DribbbleProvider
# from .sourcing.github import GitHubProvider
# from .sourcing.gitlab import GitLabProvider
# from .sourcing.googlescholar import GoogleScholarProvider
# from .sourcing.hackernews import HackerNewsProvider
# from .sourcing.hackerrank import HackerRankProvider
# from .sourcing.hashnode import HashnodeProvider
# from .sourcing.kaggle import KaggleProvider
# from .sourcing.leetcode import LeetCodeProvider
# from .sourcing.levelsfyi import LevelsFyiProvider
# from .sourcing.linkedin import LinkedInProvider
# from .sourcing.medium import MediumProvider
# from .sourcing.openstreetmap import OpenStreetMapProvider
# from .sourcing.patentdatabases import PatentDatabasesProvider
# from .sourcing.producthunt import ProductHuntProvider
# from .sourcing.reddit import RedditProvider
# from .sourcing.researchgate import ResearchGateProvider
# from .sourcing.stackoverflow import StackOverflowProvider
# from .sourcing.twitter import TwitterProvider
# from .sourcing.wellfound import WellfoundProvider

# Search engines sometimes return listing / asset / article pages (e.g. a Dribbble
# "React Frontend Page designs, themes, templates…" collection) that get scraped as
# if they were people. These keywords/patterns in the name or headline mark a result
# as NOT a real person profile so it can be dropped before display.
_NON_PERSON_KEYWORDS = (
    "design",
    "designs",
    "template",
    "templates",
    "theme",
    "themes",
    "tutorial",
    "examples",
    "example",
    "wallpaper",
    "icon",
    "icons",
    "vector",
    "stock photo",
    "free download",
    "download",
    "pricing",
    "documentation",
    " docs",
    "blog",
    "article",
    "newsletter",
    "course",
    "roadmap",
    "cheat sheet",
    "boilerplate",
    "ui kit",
    "mockup",
    "wireframe",
    "library",
    "framework",
    "snippet",
    "plugin",
    "best practices",
    "top 10",
    "browse",
    "discover",
    "explore ",
    "results for",
    "inspiration",
    "gallery",
    "collection",
    "showcase",
)


def _is_valid_profile(p: dict[str, Any]) -> bool:
    """Heuristic: keep only results that look like an actual person's profile."""
    name = str(p.get("full_name") or "").strip()
    if not name:
        return False

    low_name = name.lower()
    low_head = str(p.get("headline") or "").strip().lower()

    # Real names are short. Listing titles are long / multi-clause / truncated.
    if len(name.split()) > 6 or len(name) > 50:
        return False
    if name.endswith(("...", "…")) or "|" in name:
        return False
    if re.match(r"^\d", name):  # "3 React designs…"
        return False

    # Listing/asset keywords anywhere in the name.
    if any(k in low_name for k in _NON_PERSON_KEYWORDS):
        return False

    # Headlines that read like an article/listing.
    return not low_head.startswith(
        ("discover ", "browse ", "explore ", "top ", "best ", "shop ", "find ", "buy ")
    )


class SourcingService:
    def __init__(self):
        # Sourcing is now Claude-powered — one provider that web-searches real profiles
        # from the recruiter's query. The 27 scrapers are disabled (kept commented for
        # easy rollback).
        self.providers = {
            "claude": ClaudeSourcingProvider()
            # "github": GitHubProvider(),
            # "linkedin": LinkedInProvider(),
            # "stackoverflow": StackOverflowProvider(),
            # "devto": DevToProvider(),
            # "arxiv": ArXivProvider(),
            # "reddit": RedditProvider(),
            # "hackernews": HackerNewsProvider(),
            # "gitlab": GitLabProvider(),
            # "behance": BehanceProvider(),
            # "dribbble": DribbbleProvider(),
            # "crunchbase": CrunchbaseProvider(),
            # "hashnode": HashnodeProvider(),
            # "openstreetmap": OpenStreetMapProvider(),
            # "medium": MediumProvider(),
            # "researchgate": ResearchGateProvider(),
            # "levelsfyi": LevelsFyiProvider(),
            # "kaggle": KaggleProvider(),
            # "hackerrank": HackerRankProvider(),
            # "leetcode": LeetCodeProvider(),
            # "producthunt": ProductHuntProvider(),
            # "twitter": TwitterProvider(),
            # "wellfound": WellfoundProvider(),
            # "googlescholar": GoogleScholarProvider(),
            # "companywebsites": CompanyWebsitesProvider(),
            # "patentdatabases": PatentDatabasesProvider(),
            # "conferencespeakers": ConferenceSpeakersProvider(),
            # "academicjournals": AcademicJournalsProvider(),
        }

    def register_provider(self, name: str, provider):
        self.providers[name] = provider

    def search(
        self, platform: str, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        provider = self.providers.get(platform.lower())
        if not provider:
            return []

        results = provider.search(query, location, page, page_size)

        # Drop listing/asset/article pages that aren't real people.
        cleaned = [r for r in (results or []) if _is_valid_profile(r)]

        if results:
            dropped = len(results) - len(cleaned)
            print(f"DEBUG: Sourcing results found: {len(results)} ({dropped} non-profile dropped)")
            if cleaned:
                print(f"DEBUG: Raw data for first result: {cleaned[0].get('raw_data')}")

        return cleaned


sourcing_service = SourcingService()
