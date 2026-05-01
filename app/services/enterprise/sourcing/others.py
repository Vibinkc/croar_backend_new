from .scraper_base import BaseScraperProvider


class WellfoundProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("wellfound", "wellfound.com/u", "wellfound.com/u/")


class KaggleProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("kaggle", "kaggle.com", "kaggle.com/")


class GitLabProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("gitlab", "gitlab.com", "gitlab.com/")


class ResearchGateProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("researchgate", "researchgate.net/profile", "researchgate.net/profile/")


class MediumProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("medium", "medium.com/@", "medium.com/@")


class DevToProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("devto", "dev.to", "dev.to/")


class HackerRankProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("hackerrank", "hackerrank.com", "hackerrank.com/")


class LeetCodeProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("leetcode", "leetcode.com", "leetcode.com/")


class CrunchbaseProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("crunchbase", "crunchbase.com/person", "crunchbase.com/person/")


class ProductHuntProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("producthunt", "producthunt.com/@", "producthunt.com/@")


class DribbbleProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("dribbble", "dribbble.com", "dribbble.com/")


class RedditProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("reddit", "reddit.com/user", "reddit.com/user/")


class HackerNewsProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("hackernews", "news.ycombinator.com/user", "news.ycombinator.com/user?id=")


class LevelsFyiProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("levelsfyi", "levels.fyi", "levels.fyi/")


class GooglePatentsProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("googlepatents", "patents.google.com", "patents.google.com/")


class ArXivProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("arxiv", "arxiv.org/search", "arxiv.org/")


class CompanyWebsitesProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("company", "", "careers")  # Broad search


class ConferenceSpeakersProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("conferences", "", "speaker")


class PortfolioProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__("portfolio", "", "portfolio")
