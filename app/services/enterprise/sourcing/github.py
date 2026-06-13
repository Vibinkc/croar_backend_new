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
        # Optional token raises the GitHub API rate limit from 60/hr to 5000/hr.
        token = os.getenv("GITHUB_TOKEN")
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

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

    def _fetch(self, url: str) -> str | None:
        """Fetch a GitHub URL, preferring a DIRECT (token-authed) request.

        With GITHUB_TOKEN set, direct calls are fast and not rate-limited, so we try
        them first (a 10s cap) and only fall back to Oxylabs if the direct call fails
        (e.g. an unauthenticated rate-limit). Going Oxylabs-first made the provider take
        ~55s per search (15s Oxylabs timeout per call) and get dropped by the fan-out.
        """
        try:
            r = requests.get(url, headers=self.headers, timeout=10)
            if r.status_code == 200:
                return r.text
            print(f"DEBUG: direct GitHub fetch {url} -> {r.status_code}")
        except Exception as e:
            print(f"DEBUG: direct GitHub fetch failed for {url}: {e}")
        return self._fetch_via_oxylabs(url)

    def _get_email_from_events(self, username: str) -> str | None:
        """
        Highest fidelity hack: check the user's public activity events.
        PushEvents contain the exact email used in the git commits.
        """
        try:
            events_url = f"{self.base_url}/users/{username}/events/public"
            content = self._fetch(events_url)
            if not content:
                return None

            events = json.loads(content)
            if not isinstance(events, list):
                return None
            for event in events:
                if not isinstance(event, dict):
                    continue
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
            content = self._fetch(repos_url)
            if not content:
                return None

            repos = json.loads(content)
            if not isinstance(repos, list):
                return None
            # 2. Iterate through their own repos (not forks)
            for repo in repos:
                if isinstance(repo, dict) and not repo.get("fork") and repo.get("name"):
                    repo_name = repo["name"]
                    commits_url = f"{self.base_url}/repos/{username}/{repo_name}/commits?per_page=3"
                    commits_content = self._fetch(commits_url)

                    if commits_content:
                        commits = json.loads(commits_content)
                        if not isinstance(commits, list):
                            continue
                        for c in commits:
                            if not isinstance(c, dict):
                                continue
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
        username = str(item.get("login") or "")
        profile_url = f"https://github.com/{username}"
        avatar_url = item.get("avatar_url")

        html_content = self._fetch(profile_url)

        # Initialize fields to None
        full_name = username
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
        hireable = None

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
                        link = str(sa.get("href") or "")
                        low = link.lower()
                        if "twitter.com" in low or "x.com" in low:
                            provider = "twitter"
                            twitter_username = sa.get_text(strip=True).replace("@", "")
                        elif "linkedin.com" in low:
                            provider = "linkedin"
                        else:
                            provider = "other"
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

        # GitHub REST API enrichment (works WITHOUT Oxylabs) — fills any field the HTML
        # scrape missed and supplies the display name, public email, and hireable flag.
        api_content = self._fetch(f"{self.base_url}/users/{username}")
        if api_content:
            try:
                u = json.loads(api_content)
                if isinstance(u, dict):
                    full_name = u.get("name") or full_name
                    headline = headline or u.get("bio")
                    company = company or u.get("company")
                    location_val = location_val or u.get("location")
                    email = email or u.get("email")
                    if u.get("twitter_username"):
                        twitter_username = twitter_username or u.get("twitter_username")
                        if not any(s.get("provider") == "twitter" for s in social_links):
                            social_links.append(
                                {"provider": "twitter", "url": f"https://twitter.com/{u['twitter_username']}"}
                            )
                    b = u.get("blog")
                    if b and not blog:
                        blog = b if str(b).startswith("http") else f"https://{b}"
                    if followers is None:
                        followers = u.get("followers")
                    if following is None:
                        following = u.get("following")
                    if public_repos is None:
                        public_repos = u.get("public_repos")
                    hireable = u.get("hireable")
            except Exception as e:
                print(f"DEBUG: GitHub API enrichment failed for {username}: {e}")

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
            "full_name": full_name,
            "username": username,
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
            "hireable": hireable,
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

        # Pass query via params so special chars (&, #, spaces) are URL-encoded correctly.
        search_url = f"{self.base_url}/search/users"
        params = {"q": q, "page": page, "per_page": page_size}

        try:
            response = requests.get(search_url, headers=self.headers, params=params, timeout=10)
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
