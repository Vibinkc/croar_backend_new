from typing import Any

from .sourcing.academicjournals import AcademicJournalsProvider
from .sourcing.arxiv import ArXivProvider
from .sourcing.behance import BehanceProvider
from .sourcing.companywebsites import CompanyWebsitesProvider
from .sourcing.conferencespeakers import ConferenceSpeakersProvider
from .sourcing.crunchbase import CrunchbaseProvider
from .sourcing.devto import DevToProvider
from .sourcing.dribbble import DribbbleProvider
from .sourcing.github import GitHubProvider
from .sourcing.gitlab import GitLabProvider
from .sourcing.googlescholar import GoogleScholarProvider
from .sourcing.hackernews import HackerNewsProvider
from .sourcing.hackerrank import HackerRankProvider
from .sourcing.hashnode import HashnodeProvider
from .sourcing.kaggle import KaggleProvider
from .sourcing.leetcode import LeetCodeProvider
from .sourcing.levelsfyi import LevelsFyiProvider
from .sourcing.linkedin import LinkedInProvider
from .sourcing.medium import MediumProvider
from .sourcing.openstreetmap import OpenStreetMapProvider
from .sourcing.patentdatabases import PatentDatabasesProvider
from .sourcing.producthunt import ProductHuntProvider
from .sourcing.reddit import RedditProvider
from .sourcing.researchgate import ResearchGateProvider
from .sourcing.stackoverflow import StackOverflowProvider
from .sourcing.twitter import TwitterProvider
from .sourcing.wellfound import WellfoundProvider


class SourcingService:
    def __init__(self):
        self.providers = {
            "github": GitHubProvider(),
            "linkedin": LinkedInProvider(),
            "stackoverflow": StackOverflowProvider(),
            "devto": DevToProvider(),
            "arxiv": ArXivProvider(),
            "reddit": RedditProvider(),
            "hackernews": HackerNewsProvider(),
            "gitlab": GitLabProvider(),
            "behance": BehanceProvider(),
            "dribbble": DribbbleProvider(),
            "crunchbase": CrunchbaseProvider(),
            "hashnode": HashnodeProvider(),
            "openstreetmap": OpenStreetMapProvider(),
            "medium": MediumProvider(),
            "researchgate": ResearchGateProvider(),
            "levelsfyi": LevelsFyiProvider(),
            "kaggle": KaggleProvider(),
            "hackerrank": HackerRankProvider(),
            "leetcode": LeetCodeProvider(),
            "producthunt": ProductHuntProvider(),
            "twitter": TwitterProvider(),
            "wellfound": WellfoundProvider(),
            "googlescholar": GoogleScholarProvider(),
            "companywebsites": CompanyWebsitesProvider(),
            "patentdatabases": PatentDatabasesProvider(),
            "conferencespeakers": ConferenceSpeakersProvider(),
            "academicjournals": AcademicJournalsProvider(),
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

        # Debug: Print raw data of the first result if exists
        if results:
            print(f"DEBUG: Sourcing results found: {len(results)}")
            print(f"DEBUG: Raw data for first result: {results[0].get('raw_data')}")

        return results


sourcing_service = SourcingService()
