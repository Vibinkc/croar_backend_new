from .scraper_base import BaseScraperProvider


class GoogleScholarProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__(
            platform_name="googlescholar",
            site_domain="scholar.google.com",
            result_pattern="scholar.google.com/citations?user=",
        )
