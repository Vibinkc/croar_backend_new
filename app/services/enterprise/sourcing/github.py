import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from bs4 import BeautifulSoup

from .base import SourcingProvider


class GitHubProvider(SourcingProvider):
    def __init__(self):
        self.base_url = "https://api.github.com"
        self.headers = {"User-Agent": "Talent-Intel-App/1.0"}

    @property
    def platform_name(self) -> str:
        return "github"

    def _fetch_via_oxylabs(self, url: str) -> str | None:
        username = os.getenv("OXYLABS_USERNAME")
        password = os.getenv("OXYLABS_PASSWORD")

        if not username or not password:
            return None

        payload = {"source": "universal", "url": url}

        try:
            response = requests.post(
                "https://realtime.oxylabs.io/v1/queries", auth=(username, password), json=payload, timeout=15
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("results"):
                    return data["results"][0].get("content", "")
        except Exception as e:
            print(f"DEBUG: Oxylabs fetch failed for {url}: {e}")

        return None

    def _get_email_from_events(self, username: str) -> str | None:
        """
        Highest fidelity hack: check the user's public activity events.
        PushEvents contain the exact email used in the git commits.
        """
        try:
            events_url = f"{self.base_url}/users/{username}/events/public"
            content = self._fetch_via_oxylabs(events_url)
            if not content:
                return None

            events = json.loads(content)
            for event in events:
                if event.get("type") == "PushEvent":
                    commits = event.get("payload", {}).get("commits", [])
                    for commit in commits:
                        author = commit.get("author", {})
                        email = author.get("email")
                        if email and "@" in email and not email.endswith("@users.noreply.github.com"):
                            return email
        except Exception as e:
            print(f"DEBUG: Failed to extract email from events for {username}: {e}")
        return None

    def _get_email_from_commits(self, username: str) -> str | None:
        """
        Hack to find the user's hidden email by looking at their recent commits.
        Uses Oxylabs to bypass GitHub API rate limits.
        """
        try:
            # 1. Get recent repositories
            repos_url = f"{self.base_url}/users/{username}/repos?sort=updated&per_page=5"
            content = self._fetch_via_oxylabs(repos_url)
            if not content:
                return None

            repos = json.loads(content)
            # 2. Iterate through their own repos (not forks)
            for repo in repos:
                if not repo.get("fork") and repo.get("name"):
                    repo_name = repo["name"]
                    commits_url = f"{self.base_url}/repos/{username}/{repo_name}/commits?per_page=3"
                    commits_content = self._fetch_via_oxylabs(commits_url)

                    if commits_content:
                        commits = json.loads(commits_content)
                        for c in commits:
                            author_data = c.get("commit", {}).get("author", {})
                            email = author_data.get("email")

                            # Valid email that is not the noreply proxy
                            if email and "@" in email and not email.endswith("@users.noreply.github.com"):
                                return email
        except Exception as e:
            print(f"DEBUG: Failed to extract email from commits for {username}: {e}")

        return None

    def _scrape_profile_details(self, item: dict[str, Any], location: str | None) -> dict[str, Any]:
        """Scrapes full details for a single profile."""
        username = item.get("login")
        profile_url = f"https://github.com/{username}"
        avatar_url = item.get("avatar_url")

        html_content = self._fetch_via_oxylabs(profile_url)

        # Initialize fields to None
        company = None
        location_val = location
        email = None
        blog = None
        social_links = []
        twitter_username = None
        headline = None
        followers = None
        following = None
        public_repos = None

        if html_content:
            soup = BeautifulSoup(html_content, "html.parser")

            # Try to get the bio/headline
            bio_div = soup.find("div", class_="p-note user-profile-bio")
            if bio_div:
                headline = bio_div.get_text(strip=True)

            vcard = soup.find("ul", class_="vcard-details")
            if vcard:
                # Extract company
                org_li = vcard.find("li", attrs={"itemprop": "worksFor"})
                if org_li:
                    org_span = org_li.find("span", class_="p-org")
                    if org_span:
                        company = org_span.get_text(strip=True)

                # Extract location
                loc_li = vcard.find("li", attrs={"itemprop": "homeLocation"})
                if loc_li:
                    loc_span = loc_li.find("span", class_="p-label")
                    if loc_span:
                        location_val = loc_span.get_text(strip=True)

                # Extract email
                email_li = vcard.find("li", attrs={"itemprop": "email"})
                if email_li:
                    email_a = email_li.find("a")
                    if email_a:
                        email = email_a.get_text(strip=True)

                # Extract blog/url
                url_li = vcard.find("li", attrs={"itemprop": "url"})
                if url_li:
                    url_a = url_li.find("a")
                    if url_a:
                        blog = url_a.get("href")

                # Extract social links
                social_lis = vcard.find_all("li", attrs={"itemprop": "social"})
                for sli in social_lis:
                    sa = sli.find("a")
                    if sa:
                        link = sa.get("href")
                        provider = "twitter" if "twitter.com" in link or "x.com" in link else "other"
                        if provider == "twitter":
                            twitter_username = sa.get_text(strip=True).replace("@", "")
                        social_links.append({"provider": provider, "url": link})

            # Extract followers
            followers_a = soup.find("a", href=lambda h: h and "?tab=followers" in h)
            if followers_a:
                f_span = followers_a.find("span", class_="text-bold")
                if f_span:
                    followers = f_span.get_text(strip=True)

            # Extract following
            following_a = soup.find("a", href=lambda h: h and "?tab=following" in h)
            if following_a:
                f_span = following_a.find("span", class_="text-bold")
                if f_span:
                    following = f_span.get_text(strip=True)

            # Extract public repos
            repos_a = soup.find("a", href=lambda h: h and "?tab=repositories" in h)
            if repos_a:
                r_span = repos_a.find("span", class_="Counter")
                if r_span:
                    public_repos = r_span.get_text(strip=True)

        # Try multiple methods to find the hidden email
        if not email:
            # 1. Check Events API (fastest and very reliable)
            email = self._get_email_from_events(username)

        if not email:
            # 2. Check Commit History (good fallback)
            email = self._get_email_from_commits(username)

        # Raw data now contains the full HTML of the page
        raw_data = {"html": html_content} if html_content else {}

        return {
            "full_name": username,
            "headline": headline,
            "location": location_val,
            "platform": "github",
            "profile_url": profile_url,
            "email": email,
            "avatar_url": avatar_url,
            "company": company,
            "blog": blog,
            "twitter_username": twitter_username,
            "public_repos": public_repos,
            "followers": followers,
            "following": following,
            "hireable": None,
            "skills": [],
            "social_links": social_links,
            "raw_data": raw_data,
        }

    def search(
        self, query: str, location: str | None = None, page: int = 1, page_size: int = 15
    ) -> list[dict[str, Any]]:
        q = query
        if location:
            q += f" location:{location}"

        search_url = f"{self.base_url}/search/users?q={q}&page={page}&per_page={page_size}"

        try:
            response = requests.get(search_url, headers=self.headers, timeout=10)
            if response.status_code != 200:
                return []

            data = response.json()
            items = data.get("items", [])

            # Use ThreadPoolExecutor to scrape profiles in parallel
            profiles = []
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(self._scrape_profile_details, item, location) for item in items]
                for future in as_completed(futures):
                    try:
                        profiles.append(future.result())
                    except Exception as e:
                        print(f"DEBUG: Error processing profile: {e}")

            return profiles
        except Exception as e:
            print(f"DEBUG SEARCH ERROR: {e}")
            return []
