import asyncio
import ipaddress
import json
import os
import re
import socket
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query
from openai import OpenAI
from pydantic import BaseModel

from app.services.enterprise.sourcing_service import sourcing_service

router = APIRouter(prefix="/sourcing", tags=["Sourcing"])

# Per-platform live-search timeout (seconds). A slow or failing provider is skipped
# after this, so it can never stall the rest of the fan-out.
PLATFORM_TIMEOUT = float(os.getenv("SOURCING_PLATFORM_TIMEOUT", "10"))
# Cap on concurrent OpenAI enrichment calls so a wide fan-out doesn't burst rate limits.
ENRICH_CONCURRENCY = int(os.getenv("SOURCING_ENRICH_CONCURRENCY", "10"))
# Cap on how many providers we hit concurrently, and how many profiles we keep before
# the (per-profile) OpenAI enrichment — keeps a `platform=all` search bounded in
# latency and cost instead of fanning out unbounded work.
PROVIDER_CONCURRENCY = int(os.getenv("SOURCING_PROVIDER_CONCURRENCY", "8"))
MAX_ENRICH_PROFILES = int(os.getenv("SOURCING_MAX_ENRICH_PROFILES", "40"))

# SSRF guard: only these hosts may be fetched by the URL-taking scrape endpoints.
_ALLOWED_HOST_SUFFIXES = (
    "github.com",
    "githubusercontent.com",
    "gitlab.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "stackoverflow.com",
    "stackexchange.com",
    "dev.to",
    "medium.com",
    "kaggle.com",
    "leetcode.com",
    "hackerrank.com",
    "producthunt.com",
    "wellfound.com",
    "angel.co",
    "behance.net",
    "dribbble.com",
    "crunchbase.com",
    "hashnode.com",
    "hashnode.dev",
    "researchgate.net",
    "levels.fyi",
    "google.com",
    "arxiv.org",
    "reddit.com",
    "ycombinator.com",
    "openstreetmap.org",
)


def _is_public_host(host: str) -> bool:
    """True only if every resolved address for `host` is a public IP (blocks SSRF)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def validate_scrape_url(url: str) -> str | None:
    """Normalize + SSRF-check a user-supplied profile URL. Returns None if disallowed."""
    if not url:
        return None
    if not url.startswith("http"):
        url = f"https://{url.lstrip('/')}"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    if not any(host == s or host.endswith("." + s) for s in _ALLOWED_HOST_SUFFIXES):
        return None
    if not _is_public_host(host):
        return None
    return url


_DEFAULT_ENRICH_PROMPT = (
    "You are an AI recruitment assistant. Summarize the candidate in 1-2 powerful "
    "sentences using strong, concrete signals from their profile."
)


async def _search_single_platform(
    platform_name: str, query: str, location: str | None, page: int, page_size: int
) -> list[dict[str, Any]]:
    """Run one provider's (blocking) search in a thread, bounded by PLATFORM_TIMEOUT."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(sourcing_service.search, platform_name, query, location, page, page_size),
            timeout=PLATFORM_TIMEOUT,
        )
    except Exception as e:
        print(f"DEBUG: Sourcing platform '{platform_name}' skipped: {e}")
        return []


async def search_all_platforms(
    query: str, location: str | None = None, page: int = 1, page_size: int = 15
) -> list[dict[str, Any]]:
    """Fan out a live search across ALL registered sourcing providers, concurrently.

    Every provider is queried in parallel with a per-platform timeout; whatever responds
    in time is merged. No local store / cache is involved — results are always fresh.
    """
    platform_names = list(sourcing_service.providers.keys())
    semaphore = asyncio.Semaphore(PROVIDER_CONCURRENCY)

    async def bounded(p: str) -> list[dict[str, Any]]:
        async with semaphore:
            return await _search_single_platform(p, query, location, page, page_size)

    results = await asyncio.gather(*(bounded(p) for p in platform_names))
    merged: list[dict[str, Any]] = []
    for res in results:
        if res:
            merged.extend(res)
    merged = sanitize_profiles(merged)
    print(f"DEBUG: Live fan-out across {len(platform_names)} platforms returned {len(merged)} profiles")
    return merged


def sanitize_profiles(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop malformed provider rows and coerce required fields.

    `SourcingProfile` requires non-null `full_name` and `profile_url`; a single provider
    returning `None` for either would otherwise 500 the entire (response_model) request.
    """
    clean: list[dict[str, Any]] = []
    for p in profiles:
        if not isinstance(p, dict) or not p.get("profile_url"):
            continue
        if not p.get("full_name"):
            p["full_name"] = p.get("username") or "Unknown"
        clean.append(p)
    return clean


async def enrich_profiles(
    profiles: list[dict[str, Any]], system_prompt: str = _DEFAULT_ENRICH_PROMPT
) -> list[dict[str, Any]]:
    """Attach an AI-generated `ai_summary` to each profile, concurrently but rate-bounded.

    Enrichment is capped at MAX_ENRICH_PROFILES so a wide `platform=all` fan-out cannot
    trigger hundreds of OpenAI completions in a single request.
    """
    if not profiles:
        return profiles

    profiles = profiles[:MAX_ENRICH_PROFILES]

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    semaphore = asyncio.Semaphore(ENRICH_CONCURRENCY)

    async def enrich_one(prof: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            try:

                def call_gpt() -> str:
                    contact = f"Email: {prof.get('email')}" if prof.get("email") else "Contact: Not available"
                    completion = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {
                                "role": "user",
                                "content": (
                                    f"Name: {prof.get('full_name')}\n"
                                    f"Headline: {prof.get('headline')}\n"
                                    f"Location: {prof.get('location')}\n"
                                    f"Platform: {prof.get('platform')}\n"
                                    f"Skills: {prof.get('skills')}\n{contact}"
                                ),
                            },
                        ],
                        max_tokens=150,
                    )
                    return (completion.choices[0].message.content or "").strip()

                prof["ai_summary"] = await asyncio.to_thread(call_gpt)
            except Exception:
                prof["ai_summary"] = (
                    f"{prof.get('full_name')}, based in {prof.get('location') or 'Global'}, "
                    f"is a professional on {prof.get('platform') or 'sourcing channels'} "
                    "with established capabilities."
                )
        return prof

    return await asyncio.gather(*(enrich_one(p) for p in profiles))


def _as_str_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v if x is not None]
    if v in (None, ""):
        return []
    return [str(v)]


def _normalize_constraints(data: Any) -> dict[str, Any]:
    """Force the LLM output into the shapes the callers assume (lists / str|None)."""
    out: dict[str, Any] = dict(data) if isinstance(data, dict) else {}
    out["role_keywords"] = _as_str_list(out.get("role_keywords"))
    out["seniority_keywords"] = _as_str_list(out.get("seniority_keywords"))
    out["platform"] = out["platform"] if isinstance(out.get("platform"), str) else None
    out["location"] = out["location"] if isinstance(out.get("location"), str) else None
    return out


def parse_search_constraints(q: str) -> dict[str, Any]:
    """Use the LLM to turn a natural-language sourcing request into structured constraints."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    try:
        completion = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an AI recruitment database assistant. Extract explicit structured "
                        "search conditions from the user's natural language request as strict raw JSON "
                        "(no markdown wrapping):\n"
                        '{"role_keywords": ["frontend", "developer"], '
                        '"platform": "linkedin" | "github" | a free-form string | null, '
                        '"location": "london" | a free-form string | null, '
                        '"seniority_keywords": ["senior", "lead"]}'
                    ),
                },
                {"role": "user", "content": f"Extract constraints from: '{q}'"},
            ],
            response_format={"type": "json_object"},
            max_tokens=200,
        )
        return _normalize_constraints(json.loads((completion.choices[0].message.content or "").strip()))
    except Exception:
        return _normalize_constraints({"role_keywords": q.lower().split()})


# ---------------------------------------------------------------------------
# Contact details (free): use what providers already return, and best-effort
# deep-scrape a bounded number of em-less profiles to backfill an email/socials.
# ---------------------------------------------------------------------------

# How long a single contact deep-scrape may take, and how many em-less profiles
# to backfill per search (keeps Oxylabs cost / latency bounded).
CONTACT_SCRAPE_TIMEOUT = float(os.getenv("SOURCING_CONTACT_TIMEOUT", "15"))
CONTACT_ENRICH_LIMIT = int(os.getenv("SOURCING_CONTACT_ENRICH_LIMIT", "8"))

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# Substrings that mark a regex hit as noise rather than a real contact email.
_EMAIL_BLOCKLIST = (
    "noreply",
    "no-reply",
    "users.noreply",
    "example.com",
    "sentry",
    "wixpress",
    "godaddy",
    "@2x",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
)
_SOCIAL_MARKERS = ("linkedin.com/in", "github.com/", "gitlab.com/", "twitter.com/", "x.com/")


def _pick_email(candidates: list[str]) -> str | None:
    """Return the first plausible real email from regex/mailto candidates."""
    for raw in candidates:
        email = raw.strip().strip(".,;:()<>[]\"'")
        low = email.lower()
        if "@" not in low or any(bad in low for bad in _EMAIL_BLOCKLIST):
            continue
        return email
    return None


def _scrape_contacts(url: str) -> dict[str, Any]:
    """Best-effort fetch of a profile page to extract email + social links.

    Uses Oxylabs (rendered HTML) when credentials are present, otherwise a plain
    request. Returns empty fields on any failure — never raises.
    """
    import requests
    from bs4 import BeautifulSoup, Tag

    result: dict[str, Any] = {"email": None, "social_links": [], "blog": None}
    safe_url = validate_scrape_url(url)
    if not safe_url:
        return result
    url = safe_url

    html = ""
    user = os.getenv("OXYLABS_USERNAME")
    pwd = os.getenv("OXYLABS_PASSWORD")
    try:
        if user and pwd:
            r = requests.post(
                "https://realtime.oxylabs.io/v1/queries",
                auth=(user, pwd),
                json={"source": "universal", "url": url, "render": "html", "user_agent_type": "desktop"},
                timeout=CONTACT_SCRAPE_TIMEOUT,
            )
            if r.status_code == 200:
                res = r.json().get("results", [])
                if res:
                    html = res[0].get("content", "") or ""
        if not html:
            r = requests.get(  # nosec B113  # timeout IS set below (bandit misreads min())
                url, headers={"User-Agent": "Mozilla/5.0"}, timeout=min(CONTACT_SCRAPE_TIMEOUT, 15)
            )
            if r.status_code == 200:
                html = r.text or ""
    except Exception as e:
        print(f"DEBUG: contact scrape failed for {url}: {e}")
        return result

    if not html:
        return result

    soup = BeautifulSoup(html, "html.parser")

    # Prefer explicit mailto: links, then fall back to a regex over the page text.
    candidates: list[str] = []
    socials: list[str] = []
    for a in soup.find_all("a", href=True):
        if not isinstance(a, Tag):
            continue
        href = str(a.get("href") or "")
        if href.lower().startswith("mailto:"):
            candidates.append(href[7:].split("?")[0])
        elif any(s in href for s in _SOCIAL_MARKERS):
            if href not in socials:
                socials.append(href)
    candidates.extend(_EMAIL_RE.findall(soup.get_text(" ")))

    def _provider_of(link: str) -> str:
        low = link.lower()
        if "linkedin.com" in low:
            return "linkedin"
        if "github.com" in low:
            return "github"
        if "gitlab.com" in low:
            return "gitlab"
        if "twitter.com" in low or "x.com" in low:
            return "twitter"
        return "link"

    result["email"] = _pick_email(candidates)
    result["social_links"] = [{"provider": _provider_of(s), "url": s} for s in socials[:10]]
    return result


def _has_contact(prof: dict[str, Any]) -> bool:
    """A profile is 'reachable' if we have an email, a social link, a blog or a twitter handle."""
    return bool(
        prof.get("email") or prof.get("social_links") or prof.get("blog") or prof.get("twitter_username")
    )


async def backfill_contacts(profiles: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    """Deep-scrape contact info for up to `limit` profiles that have no email yet."""
    if limit is None:
        limit = CONTACT_ENRICH_LIMIT
    targets = [p for p in profiles if not p.get("email") and p.get("profile_url")][:limit]
    if not targets:
        return profiles

    semaphore = asyncio.Semaphore(min(ENRICH_CONCURRENCY, 6))

    async def fill_one(prof: dict[str, Any]) -> None:
        async with semaphore:
            try:
                contacts = await asyncio.wait_for(
                    asyncio.to_thread(_scrape_contacts, str(prof.get("profile_url") or "")),
                    timeout=CONTACT_SCRAPE_TIMEOUT + 5,
                )
            except Exception:
                return
            if contacts.get("email") and not prof.get("email"):
                prof["email"] = contacts["email"]
            if contacts.get("social_links") and not prof.get("social_links"):
                prof["social_links"] = contacts["social_links"]
            if contacts.get("blog") and not prof.get("blog"):
                prof["blog"] = contacts["blog"]

    await asyncio.gather(*(fill_one(p) for p in targets))
    print(f"DEBUG: Backfilled contacts for up to {len(targets)} em-less profiles")
    return profiles


class SourcingProfile(BaseModel):
    full_name: str
    headline: str | None = None
    location: str | None = None
    platform: str
    profile_url: str
    email: str | None = None
    avatar_url: str | None = None
    company: str | None = None
    blog: str | None = None
    twitter_username: str | None = None
    public_repos: int | str | None = None
    followers: int | str | None = None
    following: int | str | None = None
    hireable: bool | None = None
    skills: list[str] = []
    social_links: list[dict[str, str]] = []
    ai_summary: str | None = None
    raw_data: dict[str, Any] = {}


@router.get("/search", response_model=list[SourcingProfile])
async def search_profiles(
    q: str = Query(..., description="The search query"),
    location: str | None = Query(None, description="Location filter"),
    platform: str = Query("github", description="Sourcing platform"),
    page: int = Query(1, ge=1),
    page_size: int = Query(15, ge=1, le=100),
    enrich_contacts: bool = Query(True, description="Deep-scrape missing emails/socials"),
    has_contact: bool = Query(False, description="Only return profiles that have contact info"),
):
    """
    Search for professional profiles across multiple platforms.
    """
    # "all" -> live fan-out across every registered provider; otherwise a single platform.
    if platform == "all":
        profiles = await search_all_platforms(q, location, page, page_size)
    else:
        profiles = sanitize_profiles(await _search_single_platform(platform, q, location, page, page_size))

    if enrich_contacts:
        profiles = await backfill_contacts(profiles)
    if has_contact:
        profiles = [p for p in profiles if _has_contact(p)]

    # Surface candidates with a direct email first (stable: keeps relevance within groups).
    profiles.sort(key=lambda p: 0 if p.get("email") else 1)

    return await enrich_profiles(profiles)


@router.get("/chat_db")
async def chat_search_profiles(
    q: str = Query(..., description="The chat prompt query"),
    page: int = Query(1, description="Page index"),
    limit: int = Query(10, description="Items per page"),
    enrich_contacts: bool = Query(True, description="Deep-scrape missing emails/socials"),
    has_contact: bool = Query(False, description="Only return profiles that have contact info"),
):
    """Conversational sourcing search.

    Parses the natural-language prompt into structured constraints, then searches live
    across all sourcing platforms (or a single platform if the prompt names one).
    No local store / cache is involved.
    """
    try:
        gpt_data = await asyncio.to_thread(parse_search_constraints, q)

        search_query = (
            " ".join(gpt_data.get("role_keywords", []) + gpt_data.get("seniority_keywords", [])).strip() or q
        )
        location_str = gpt_data.get("location")
        target_platform = gpt_data.get("platform")

        if target_platform and str(target_platform).lower() in sourcing_service.providers:
            profiles = sanitize_profiles(
                await _search_single_platform(
                    str(target_platform).lower(), search_query, location_str, page, limit
                )
            )
        else:
            profiles = await search_all_platforms(search_query, location_str, page, limit)

        if enrich_contacts:
            profiles = await backfill_contacts(profiles)
        if has_contact:
            profiles = [p for p in profiles if _has_contact(p)]

        # Surface candidates with a direct email first.
        profiles.sort(key=lambda p: 0 if p.get("email") else 1)

        if not profiles:
            return {
                "response": "No matching profiles found across the connected platforms. "
                "Try loosening your keywords or location.",
                "profiles": [],
                "total_count": 0,
            }

        total_count = len(profiles)
        summarized = await enrich_profiles(
            profiles,
            system_prompt=(
                "You are an AI recruitment consultant. Write a highly professional, engaging "
                "1-2 sentence assessment. If an email is provided, mention that direct contact is available."
            ),
        )

        response_msg = (
            f"I searched the connected platforms live and found {total_count} matching profiles, "
            "including those with direct contact info."
        )
        return {"response": response_msg, "profiles": summarized, "total_count": total_count}
    except Exception as e:
        return {"response": f"Live sourcing search failed: {e}", "profiles": [], "total_count": 0}


@router.get("/chat_distribution")
async def get_chat_distribution(q: str = Query(..., description="The chat prompt query")):
    """Return the live location distribution for a query, computed from real-time results."""
    try:
        gpt_data = await asyncio.to_thread(parse_search_constraints, q)
        search_query = (
            " ".join(gpt_data.get("role_keywords", []) + gpt_data.get("seniority_keywords", [])).strip() or q
        )
        location_str = gpt_data.get("location")
        target_platform = gpt_data.get("platform")

        if target_platform and str(target_platform).lower() in sourcing_service.providers:
            profiles = await _search_single_platform(
                str(target_platform).lower(), search_query, location_str, 1, 50
            )
        else:
            profiles = await search_all_platforms(search_query, location_str, 1, 50)

        counts: dict[str, int] = {}
        for prof in profiles:
            loc = prof.get("location") or "Unknown"
            counts[loc] = counts.get(loc, 0) + 1

        distribution = [{"location": loc, "count": c} for loc, c in counts.items()]
        distribution.sort(key=lambda d: d["count"], reverse=True)
        return distribution
    except Exception as e:
        print(f"Error in distribution: {e}")
        return []


@router.get("/contact_details")
async def get_contact_details(url: str = Query(..., description="The candidate's profile URL")):
    """On-demand deep-scrape of a single profile URL for email + social links.

    Backs a "Reveal contact" action in the UI so the heavy scrape only runs when a
    recruiter actually wants a specific candidate's contact info.
    """
    contacts = await asyncio.to_thread(_scrape_contacts, url)
    return contacts


@router.get("/profile_details")
async def get_profile_details(url: str = Query(..., description="The direct profile URL")):
    """Scrape rich public details from a profile URL (SSRF-guarded, off the event loop)."""
    # Normalize relative / localized forms BEFORE the SSRF check.
    if not url.startswith("http"):
        url = f"https://www.kaggle.com{url}" if url.startswith("/") else f"https://{url}"
    if "linkedin.com" in url:
        url = re.sub(r"https?://[a-z]{2,3}\.linkedin\.com", "https://www.linkedin.com", url)

    safe_url = validate_scrape_url(url)
    if not safe_url:
        raise HTTPException(status_code=400, detail="URL host not allowed")

    # The scraper does blocking `requests` IO, so run it in a worker thread.
    return await asyncio.to_thread(_profile_details_impl, safe_url)


def _profile_details_impl(url: str) -> dict:  # pyright: ignore[reportGeneralTypeIssues]
    """Synchronous profile scraper — must run off the event loop (blocking requests).

    This legacy multi-platform HTML scraper has many conditional branches; the
    pyright "too complex to analyze" inference is suppressed here (the logic is
    exercised by the live sourcing tests, not the type-checker).
    """
    import os

    import requests
    from bs4 import BeautifulSoup

    # Let GitLab profiles use the standard Oxylabs HTML parser for rich data extraction

    # Intercept Kaggle discussion threads and resolve to the author's profile URL
    if "kaggle.com/discussions/" in url:
        username = os.getenv("OXYLABS_USERNAME")
        password = os.getenv("OXYLABS_PASSWORD")
        if username and password:
            disc_payload = {"source": "universal", "url": url, "render": "html", "user_agent_type": "desktop"}
            try:
                disc_res = requests.post(
                    "https://realtime.oxylabs.io/v1/queries",
                    auth=(username, password),
                    json=disc_payload,
                    timeout=60,
                )
                if disc_res.status_code == 200:
                    disc_data = disc_res.json()
                    disc_results = disc_data.get("results", [])
                    if disc_results:
                        disc_html = disc_results[0].get("content", "")
                        if disc_html:
                            disc_soup: Any = BeautifulSoup(disc_html, "html.parser")
                            # Look for relative profile hrefs in the discussion
                            author_links = []
                            for a in disc_soup.find_all("a", href=True):
                                href = a["href"]
                                # Avoid standard path fragments
                                if href.startswith("/") and not any(
                                    k in href
                                    for k in [
                                        "/discussions",
                                        "/competitions",
                                        "/docs/",
                                        "/code/",
                                        "/learn",
                                        "/datasets",
                                        "/models",
                                        "/organizations",
                                        "/edu",
                                    ]
                                ):
                                    if href.count("/") == 1 and len(href) > 2:
                                        author_links.append(href)

                            if author_links:
                                print(
                                    f"DEBUG: Found Kaggle discussion author relative link: {author_links[0]}"
                                )
                                url = f"https://www.kaggle.com{author_links[0]}"
                                # Cache check removed
                                pass
            except Exception as e:
                print(f"DEBUG: Kaggle discussion resolution failed: {e}")

    # Cache check removed

    username = os.getenv("OXYLABS_USERNAME")
    password = os.getenv("OXYLABS_PASSWORD")

    payload = {"source": "universal", "url": url, "render": "html", "user_agent_type": "desktop"}

    try:
        print(f"DEBUG: Scraping detailed profile info for {url}")
        max_retries = 3
        r = None
        html_content = ""

        for attempt in range(max_retries):
            try:
                if username and password:
                    r = requests.post(
                        "https://realtime.oxylabs.io/v1/queries",
                        auth=(username, password),
                        json=payload,
                        timeout=60,
                    )
                    if r.status_code == 200:
                        data = r.json()
                        results = data.get("results", [])
                        if results:
                            html_content = results[0].get("content", "")
                            if html_content and len(html_content.strip()) > 50:
                                break

                # Local direct request fallback
                if not html_content:
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    }
                    fallback_res = requests.get(url, headers=headers, timeout=15)
                    if fallback_res.status_code == 200:
                        html_content = fallback_res.text
                        if html_content and len(html_content.strip()) > 50:
                            break

                print(f"DEBUG: Scraper returned blank content on attempt {attempt + 1}/{max_retries}")
            except Exception as e:
                print(f"DEBUG: Exception on attempt {attempt + 1}/{max_retries}: {e}")
                if attempt == max_retries - 1:
                    # Final attempt local direct fallback just in case
                    try:
                        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                        fallback_res = requests.get(url, headers=headers, timeout=15)
                        if fallback_res.status_code == 200:
                            html_content = fallback_res.text
                            break
                    except Exception:
                        raise e from None

        if not html_content:
            return {"error": "Failed to extract readable public profile details after retries."}

        html = html_content
        if not html:
            return {"error": "Empty HTML content received"}

        # Typed as Any: this legacy scraper walks deeply-optional BeautifulSoup nodes;
        # Any keeps the (well-guarded) traversal readable without a wall of type errors.
        soup: Any = BeautifulSoup(html, "html.parser")

        sections = []

        # 1. Attempt precise component extraction
        section_elements = soup.find_all("section")
        extracted_keys = set()

        noise_phrases = [
            "Sign in to view",
            "Already on LinkedIn?",
            "Join now to view",
            "full profile",
            "Welcome back",
            "Forgot password?",
            "Sign in",
            "Email or phone",
            "Continue with Google",
            "New to LinkedIn? Join now",
            "By clicking Continue to join or sign in",
            "No more previous content",
            "No more next content",
            "Report this post",
            "can introduce you to",
            "Password",
            "Join with email",
            "LINKEDIN RESPECTS YOUR PRIVACY",
            "Cookie Policy",
            "Select Accept to consent",
            "non-essential cookies",
            "Show",
            "Accept",
            "Reject",
        ]

        def clean_section_lines(sec_element):
            lines = []
            for line in sec_element.text.split("\n"):
                l_clean = line.strip()
                if l_clean and len(l_clean) > 2 and "********" not in l_clean:
                    if any(phrase in l_clean for phrase in noise_phrases):
                        continue
                    if "commented on a post" in l_clean or "reacted on this" in l_clean:
                        continue
                    if l_clean not in lines:
                        lines.append(l_clean)
            return lines

        keyword_map = {
            "Topcard": "TOPCARD",
            "About": "ABOUT",
            "Experience": "EXPERIENCE",
            "Education": "EDUCATION",
            "Certification": "LICENSES & CERTIFICATIONS",
            "Volunteer": "VOLUNTEER EXPERIENCE",
            "Skills": "SKILLS",
            "Recommendations": "RECOMMENDATIONS",
            "Projects": "PROJECTS",
            "Language": "LANGUAGES",
            "Organizations": "ORGANIZATIONS",
            "Publication": "PUBLICATIONS",
            "Patents": "PATENTS",
            "Courses": "COURSES",
            "Honors": "HONORS & AWARDS",
        }

        for sec in section_elements:
            comp_key = sec.get("componentkey", "")
            if not comp_key:
                continue

            for keyword, title in keyword_map.items():
                if keyword in comp_key and title not in extracted_keys:
                    lines = clean_section_lines(sec)
                    if lines:
                        if title == "TOPCARD":
                            sections.append("\n".join(lines))
                        else:
                            sections.append(f"{title}\n" + "\n".join(lines))
                        extracted_keys.add(title)
                    break

        # 2. Try pulling via <h2> headers if component keys didn't yield sections
        if not sections:
            for sec in section_elements:
                h2 = sec.find("h2")
                if h2:
                    sec_title = h2.text.strip().upper()
                    if sec_title in ["ACTIVITY", "POSTS", "INTERESTS", "CAUSES"]:
                        continue
                    lines = clean_section_lines(sec)
                    if lines and lines[0].upper() == sec_title:
                        lines = lines[1:]
                    if lines:
                        sections.append(f"{sec_title}\n" + "\n".join(lines))
                        extracted_keys.add(sec_title)

            # Check ResearchGate specific profile content items
            rg_items = soup.find_all("div", class_="profile-content-item")
            for item in rg_items:
                h2 = item.find("h2")
                if h2:
                    sec_title = h2.text.strip().upper()
                    lines = clean_section_lines(item)
                    if lines and lines[0].upper() == sec_title:
                        lines = lines[1:]
                    if lines:
                        sections.append(f"{sec_title}\n" + "\n".join(lines))
                        extracted_keys.add(sec_title)

            # Check Crunchbase specific profile content cards
            cb_cards = soup.find_all("mat-card")
            for card in cb_cards:
                title_div = card.find(class_="section-title")
                if not title_div:
                    title_div = card.find("h2")
                if title_div:
                    sec_title = title_div.text.strip().upper()
                    lines = clean_section_lines(card)
                    if lines and lines[0].upper() == sec_title:
                        lines = lines[1:]
                    if lines:
                        sections.append(f"{sec_title}\n" + "\n".join(lines))
                        extracted_keys.add(sec_title)

            # Check Levels.fyi specific structure patterns
            levels_blocks = soup.find_all(["div", "section"])
            for block in levels_blocks:
                text_content = block.text.lower()
                if "base salary" in text_content or "total compensation" in text_content:
                    h2 = block.find(["h1", "h2", "h3"])
                    sec_title = h2.text.strip().upper() if h2 else "COMPENSATION DATA"
                    if sec_title in extracted_keys:
                        continue
                    lines = clean_section_lines(block)
                    if lines and lines[0].upper() == sec_title:
                        lines = lines[1:]
                    if lines:
                        sections.append(f"{sec_title}\n" + "\n".join(lines))
                        extracted_keys.add(sec_title)

            # Check GitLab specific structure patterns
            gl_header = soup.find("div", class_="user-profile-header")
            gl_flex = soup.find("div", class_="gl-flex")

            if gl_header or gl_flex:
                gl_name = "GitLab Profile"
                if gl_header:
                    gl_name_el = gl_header.find(["h1", "h2"])
                    gl_name = gl_name_el.text.strip() if gl_name_el else "GitLab Profile"
                elif gl_flex:
                    gl_name_el = soup.find(["h1", "h2"], itemprop="name")
                    if gl_name_el:
                        gl_name = gl_name_el.text.strip()

                gl_bio = ""
                if gl_header:
                    gl_bio_el = gl_header.find("div", class_="cover-status")
                    gl_bio = gl_bio_el.text.strip() if gl_bio_el else ""

                # Parse additional GitLab Info block
                gl_job = soup.find(itemprop="jobTitle")
                gl_company = soup.find(itemprop="worksFor")
                gl_location = soup.find(itemprop="addressLocality")
                gl_email = soup.find(itemprop="email")
                gl_url = soup.find("a", itemprop="url")

                job_str = gl_job.text.strip() if gl_job else ""
                company_str = gl_company.text.strip() if gl_company else ""
                location_str = gl_location.text.strip() if gl_location else ""
                email_str = gl_email.text.strip() if gl_email else ""
                url_str = gl_url.get("href", "").strip() if gl_url else ""

                # Check for social links
                social_links = []
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "linkedin.com/" in href or "twitter.com/" in href or "github.com/" in href:
                        if href not in social_links:
                            social_links.append(href)

                gl_readme_el = soup.find("div", class_="file-content")
                gl_readme = gl_readme_el.text.strip() if gl_readme_el else ""

                gl_lines = [
                    f"NAME: {gl_name}",
                    f"ROLE: {job_str}" if job_str else "",
                    f"COMPANY: {company_str}" if company_str else "",
                    f"LOCATION: {location_str}" if location_str else "",
                    f"EMAIL: {email_str}" if email_str else "",
                    f"WEBSITE: {url_str}" if url_str else "",
                    f"BIO: {gl_bio}" if gl_bio else "",
                    f"SOCIALS: {', '.join(social_links)}" if social_links else "",
                    f"README INFO:\n{gl_readme}" if gl_readme else "",
                ]
                gl_lines = [ln for ln in gl_lines if ln]
                if gl_lines:
                    sections.append("GITLAB PROFILE\n" + "\n".join(gl_lines))
                    extracted_keys.add("GITLAB PROFILE")

            # Check Kaggle specific structure patterns
            if "kaggle.com/" in url:
                ka_name_el = soup.find("h1") or soup.find("div", class_="sc-fujBio")
                ka_name = ka_name_el.text.strip() if ka_name_el else "Kaggle Profile"

                # Username
                ka_user_el = soup.find("p", class_="sc-fFSRQT bDmssn") or soup.find("p", class_="bDmssn")
                ka_user = ka_user_el.text.strip() if ka_user_el else ""

                # Bio extraction
                ka_bio_el = soup.find("div", class_="sc-dFRqiS dFmmBg") or soup.find("div", class_="dFmmBg")
                ka_bio = ka_bio_el.text.strip() if ka_bio_el else ""

                # Role and Location
                ka_role = ""
                ka_loc = ""
                # Find all jcjPkd spans/paragraphs
                pkd_items = soup.find_all(["p", "span"], class_="jcjPkd")
                for item in pkd_items:
                    text = item.text.strip()
                    # Use simple logic to determine role vs location
                    if any(
                        loc_k in text.lower()
                        for loc_k in [
                            "united states",
                            "india",
                            "chicago",
                            "state",
                            "city",
                            "germany",
                            "uk",
                            "canada",
                            "london",
                            "australia",
                        ]
                    ):
                        ka_loc = text
                    elif len(text) > 2 and not ka_role:
                        ka_role = text

                # Metrics and Achievements
                ka_metrics = []
                ka_tier_el = soup.find("p", class_="kAUsEY")
                if ka_tier_el:
                    ka_metrics.append(f"TIER: {ka_tier_el.text.strip()}")

                for div in soup.find_all("div"):
                    text = div.text.strip()
                    if "Followers" in text or "Following" in text or "Competitions" in text:
                        if text and len(text) < 50 and text not in ka_metrics:
                            ka_metrics.append(text)

                # Social Links
                ka_socials = []
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if any(
                        soc in href
                        for soc in ["github.com", "linkedin.com", "twitter.com", "carrd.co", "x.com"]
                    ):
                        if href not in ka_socials:
                            ka_socials.append(href)

                ka_lines = [
                    f"NAME: {ka_name}",
                    f"USERNAME: @{ka_user}" if ka_user else "",
                    f"ROLE: {ka_role}" if ka_role else "",
                    f"LOCATION: {ka_loc}" if ka_loc else "",
                    f"BIO: {ka_bio}" if ka_bio else "",
                    f"SOCIALS: {', '.join(ka_socials)}" if ka_socials else "",
                    f"METRICS: {', '.join(ka_metrics)}" if ka_metrics else "",
                ]
                ka_lines = [ln for ln in ka_lines if ln]
                if ka_lines:
                    sections.append("KAGGLE PROFILE\n" + "\n".join(ka_lines))
                    extracted_keys.add("KAGGLE PROFILE")

            # Check HackerRank specific structure patterns
            if "hackerrank.com/" in url:
                hr_name_el = soup.find("h1", class_="profile-title") or soup.find("h1")
                hr_name = hr_name_el.text.strip() if hr_name_el else "HackerRank Profile"

                hr_user_el = soup.find("p", class_="profile-username-heading")
                hr_user = hr_user_el.text.strip() if hr_user_el else ""

                hr_resume_el = soup.find("a", class_="profile-resume-text")
                hr_resume = hr_resume_el.get("href", "").strip() if hr_resume_el else ""

                # Badges
                badges = []
                badge_els = soup.find_all(["text", "span"], class_="badge-title")
                for badge in badge_els:
                    badges.append(badge.text.strip())

                hr_meta = []
                if badges:
                    hr_meta.append(f"BADGES: {', '.join(badges)}")

                for span in soup.find_all(["span", "div"]):
                    text = span.text.strip()
                    if any(k in text for k in ["Rank", "Points", "Badges", "Solved"]) and len(text) < 40:
                        if text not in hr_meta:
                            hr_meta.append(text)

                hr_socials = []
                if hr_resume:
                    hr_socials.append(f"RESUME: {hr_resume}")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if any(soc in href for soc in ["github.com", "linkedin.com", "twitter.com"]):
                        if href not in hr_socials:
                            hr_socials.append(href)

                hr_lines = [
                    f"NAME: {hr_name}",
                    f"USERNAME: {hr_user}" if hr_user else "",
                    f"METRICS: {', '.join(hr_meta)}" if hr_meta else "",
                    f"SOCIALS: {', '.join(hr_socials)}" if hr_socials else "",
                ]
                hr_lines = [ln for ln in hr_lines if ln]
                if hr_lines:
                    sections.append("HACKERRANK PROFILE\n" + "\n".join(hr_lines))
                    extracted_keys.add("HACKERRANK PROFILE")

            # Check LeetCode specific structure patterns
            if "leetcode.com/" in url:
                lc_name_el = soup.find("div", class_="text-label-1") or soup.find("div", class_="text-xl")
                lc_name = lc_name_el.text.strip() if lc_name_el else "LeetCode Profile"

                lc_user_el = soup.find("span", class_="text-label-3") or soup.find("span", class_="text-sm")
                lc_user = lc_user_el.text.strip() if lc_user_el else ""

                lc_meta = []
                for div in soup.find_all("div"):
                    text = div.text.strip()
                    if (
                        any(k in text for k in ["Rank", "Solved", "Beats", "Contest Rating"])
                        and len(text) < 40
                    ):
                        if text not in lc_meta:
                            lc_meta.append(text)

                lc_socials = []
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if any(soc in href for soc in ["github.com", "linkedin.com", "twitter.com"]):
                        if href not in lc_socials:
                            lc_socials.append(href)

                lc_lines = [
                    f"NAME: {lc_name}",
                    f"USERNAME: {lc_user}" if lc_user else "",
                    f"METRICS: {', '.join(lc_meta)}" if lc_meta else "",
                    f"SOCIALS: {', '.join(lc_socials)}" if lc_socials else "",
                ]
                lc_lines = [ln for ln in lc_lines if ln]
                if lc_lines:
                    sections.append("LEETCODE PROFILE\n" + "\n".join(lc_lines))
                    extracted_keys.add("LEETCODE PROFILE")

            # Check Product Hunt specific structure patterns
            if "producthunt.com/" in url:
                ph_name_el = soup.find("h1", class_="text-dark-gray") or soup.find("h1")
                ph_name = ph_name_el.text.strip() if ph_name_el else "Product Hunt Profile"

                ph_bio_el = soup.find("span", class_="text-light-gray") or soup.find("div", class_="text-16")
                ph_bio = ph_bio_el.text.strip() if ph_bio_el else ""

                ph_meta = []
                for a in soup.find_all("a", href=True):
                    text = a.text.strip()
                    if "followers" in text.lower() or "following" in text.lower():
                        ph_meta.append(text)

                kp_els = soup.find_all("span", class_="text-brand-500")
                for kp in kp_els:
                    ph_meta.append(f"POINTS: {kp.text.strip()}")

                for div in soup.find_all(["div", "span"]):
                    text = div.text.strip()
                    if any(k in text for k in ["Upvotes", "Products", "Streak"]) and len(text) < 40:
                        if text not in ph_meta:
                            ph_meta.append(text)

                ph_socials = []
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if any(soc in href for soc in ["github.com", "linkedin.com", "twitter.com", "x.com"]) or (
                        href.startswith("http") and "producthunt.com" not in href
                    ):
                        if href not in ph_socials:
                            ph_socials.append(href)

                ph_lines = [
                    f"NAME: {ph_name}",
                    f"BIO: {ph_bio}" if ph_bio else "",
                    f"METRICS: {', '.join(ph_meta)}" if ph_meta else "",
                    f"SOCIALS: {', '.join(ph_socials)}" if ph_socials else "",
                ]
                ph_lines = [ln for ln in ph_lines if ln]
                if ph_lines:
                    sections.append("PRODUCT HUNT PROFILE\n" + "\n".join(ph_lines))
                    extracted_keys.add("PRODUCT HUNT PROFILE")
            # Check Twitter specific structure patterns
            if "twitter.com/" in url or "x.com/" in url:
                tw_name_el = soup.find("div", {"data-testid": "UserName"})
                tw_name = ""
                if tw_name_el:
                    spans = tw_name_el.find_all("span")
                    if spans:
                        tw_name = spans[0].text.strip()
                if not tw_name:
                    tw_name_el = soup.find("h1")
                    tw_name = tw_name_el.text.strip() if tw_name_el else "Twitter Profile"

                tw_desc_el = soup.find("div", {"data-testid": "UserDescription"})
                tw_desc = tw_desc_el.text.strip() if tw_desc_el else ""

                tw_loc_el = soup.find("span", {"data-testid": "UserLocation"})
                tw_loc = tw_loc_el.text.strip() if tw_loc_el else ""

                tw_url_el = soup.find("a", {"data-testid": "UserUrl"})
                tw_url = tw_url_el.text.strip() if tw_url_el else ""

                tw_meta = []
                if tw_loc:
                    tw_meta.append(f"LOCATION: {tw_loc}")
                if tw_url:
                    tw_meta.append(f"WEBSITE: {tw_url}")

                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "following" in href.lower() or "followers" in href.lower():
                        text = a.text.strip()
                        if text and text not in tw_meta:
                            tw_meta.append(text)

                def meta_tag(prop):
                    tag = soup.find("meta", {"property": prop}) or soup.find("meta", {"name": prop})
                    return tag.get("content").strip() if tag and tag.get("content") else ""

                if not tw_desc:
                    tw_desc = meta_tag("og:description") or meta_tag("description")
                tw_img = meta_tag("og:image")

                if tw_name == "Twitter Profile" or not tw_name:
                    og_title = meta_tag("og:title")
                    if og_title:
                        tw_name = og_title.split("(")[0].strip()
                        tw_name = tw_name.replace(" / X", "").replace(" on Twitter", "").strip()
                if not tw_name:
                    tw_name = "Twitter Profile"

                tw_lines = [
                    f"NAME: {tw_name}",
                    f"BIO: {tw_desc}" if tw_desc else "",
                    f"METRICS: {', '.join(tw_meta)}" if tw_meta else "",
                    f"AVATAR: {tw_img}" if tw_img else "",
                ]
                tw_lines = [ln for ln in tw_lines if ln]
                if tw_lines:
                    sections.append("TWITTER PROFILE\n" + "\n".join(tw_lines))
                    extracted_keys.add("TWITTER PROFILE")

            # Check Wellfound specific structure patterns
            if "wellfound.com/" in url or "angel.co/" in url:
                wf_name_el = soup.find("h1") or soup.find("div", class_="text-32")
                wf_name = wf_name_el.text.strip() if wf_name_el else "Wellfound Profile"

                def meta_tag(prop):
                    tag = soup.find("meta", {"property": prop}) or soup.find("meta", {"name": prop})
                    return tag.get("content").strip() if tag and tag.get("content") else ""

                wf_desc = meta_tag("og:description") or meta_tag("description")

                wf_lines = [f"NAME: {wf_name}", f"BIO: {wf_desc}" if wf_desc else ""]
                wf_lines = [ln for ln in wf_lines if ln]
                if wf_lines:
                    sections.append("WELLFOUND PROFILE\n" + "\n".join(wf_lines))
                    extracted_keys.add("WELLFOUND PROFILE")

            # Check Dribbble specific structure patterns
            if "dribbble.com/" in url:
                dr_name_el = soup.find("h1") or soup.find("h2", class_="name")
                dr_name = dr_name_el.text.strip() if dr_name_el else "Dribbble Profile"

                def meta_tag(prop):
                    tag = soup.find("meta", {"property": prop}) or soup.find("meta", {"name": prop})
                    return tag.get("content").strip() if tag and tag.get("content") else ""

                dr_desc = meta_tag("og:description") or meta_tag("description")

                dr_lines = [f"NAME: {dr_name}", f"BIO: {dr_desc}" if dr_desc else ""]
                dr_lines = [ln for ln in dr_lines if ln]
                if dr_lines:
                    sections.append("DRIBBBLE PROFILE\n" + "\n".join(dr_lines))
                    extracted_keys.add("DRIBBBLE PROFILE")

        # 2. Fallback to parsing general text if no structured blocks found
        if not sections:
            lazy_column = soup.find("div", {"data-testid": "lazy-column"})
            main_content = soup.find("main")

            if lazy_column:
                raw_text = lazy_column.text
            elif main_content:
                raw_text = main_content.text
            else:
                raw_text = soup.text

            paragraphs = [p.strip() for p in raw_text.split("\n") if p.strip()]
            filtered_paragraphs = []
            for p in paragraphs:
                if any(n in p for n in ["********"]):
                    continue
                if any(phrase in p for phrase in noise_phrases):
                    continue
                if "commented on a post" in p or "reacted on this" in p:
                    continue
                if len(p) > 15 and p not in filtered_paragraphs:
                    filtered_paragraphs.append(p)

            current_block = []
            for p in filtered_paragraphs:
                current_block.append(p)
                if len(current_block) >= 5:
                    sections.append("\n".join(current_block))
                    current_block = []
            if current_block:
                sections.append("\n".join(current_block))

        res = {
            "title": soup.title.string if soup.title else "Scraped Profile",
            "sections": sections if sections else ["No detailed public text extracted."],
            "url": url,
        }
        # Caching removed as per user request
        return res
    except Exception as e:
        print(f"DEBUG: Profile scrape failed: {e}")
        return {"error": str(e)}
