from .scraper_base import BaseScraperProvider


class CompanyWebsitesProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__(
            platform_name="companywebsites",
            site_domain="linkedin.com/company",
            result_pattern="linkedin.com/company/",
        )
