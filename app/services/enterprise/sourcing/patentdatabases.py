from .scraper_base import BaseScraperProvider


class PatentDatabasesProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__(
            platform_name="patentdatabases",
            site_domain="patents.google.com",
            result_pattern="patents.google.com/patent/",
        )
