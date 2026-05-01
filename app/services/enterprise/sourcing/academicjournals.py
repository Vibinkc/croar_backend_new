from .scraper_base import BaseScraperProvider


class AcademicJournalsProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__(
            platform_name="academicjournals",
            site_domain="researchgate.net/publication",
            result_pattern="researchgate.net/publication/",
        )
