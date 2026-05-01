from .scraper_base import BaseScraperProvider


class ConferenceSpeakersProvider(BaseScraperProvider):
    def __init__(self):
        super().__init__(
            platform_name="conferencespeakers",
            site_domain="sessionize.com",
            result_pattern="sessionize.com/speaker/",
        )
