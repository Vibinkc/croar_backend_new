from .scraper_base import BaseScraperProvider


class BehanceProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__(platform_name="behance", site_domain="behance.net", result_pattern="behance.net/")
