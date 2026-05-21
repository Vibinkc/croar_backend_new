from typing import Any

from fastapi import APIRouter, Query, BackgroundTasks
from pydantic import BaseModel

from app.services.enterprise.sourcing_service import sourcing_service

router = APIRouter(prefix="/sourcing", tags=["Sourcing"])


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
):
    """
    Search for professional profiles across multiple platforms.
    """
    if platform == "all":
        import asyncio

        top_platforms = ["github", "linkedin", "twitter", "stackoverflow", "wellfound"]
        all_profiles = []

        async def fetch_platform(p):
            try:
                return await asyncio.to_thread(sourcing_service.search, p, q, location, page, page_size)
            except Exception:
                return []

        results = await asyncio.gather(*(fetch_platform(p) for p in top_platforms))
        for res in results:
            if res:
                all_profiles.extend(res)

        import os

        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        async def enrich_profile(prof):
            try:

                def call_gpt():
                    completion = client.chat.completions.create(
                        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                        messages=[
                            {
                                "role": "system",
                                "content": "You are an AI recruitment assistant. Summarize the candidate in 1-2 powerful sentences using strong metrics.",
                            },
                            {
                                "role": "user",
                                "content": f"Name: {prof.get('full_name')}\nHeadline: {prof.get('headline')}\nLocation: {prof.get('location')}\nPlatform: {prof.get('platform')}",
                            },
                        ],
                        max_tokens=150,
                    )
                    return completion.choices[0].message.content.strip()

                prof["ai_summary"] = await asyncio.to_thread(call_gpt)
            except Exception:
                prof["ai_summary"] = (
                    f"{prof.get('full_name')}, based in {prof.get('location') or 'Global'}, is a professional on {prof.get('platform') or 'sourcing channels'} with established capabilities."
                )
            return prof

        tasks = [enrich_profile(p) for p in all_profiles]
        enriched_all = await asyncio.gather(*tasks)
        return enriched_all

    # Caching removed as per user request
    profiles = sourcing_service.search(platform, q, location, page, page_size)

    import os

    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    async def enrich_single(prof):
        try:

            def call_gpt():
                completion = client.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    messages=[
                        {
                            "role": "system",
                            "content": "You are an AI recruitment assistant. Summarize the candidate in 1-2 powerful sentences.",
                        },
                        {
                            "role": "user",
                            "content": f"Name: {prof.get('full_name')}\nHeadline: {prof.get('headline')}\nLocation: {prof.get('location')}\nPlatform: {prof.get('platform')}",
                        },
                    ],
                    max_tokens=150,
                )
                return completion.choices[0].message.content.strip()

            prof["ai_summary"] = await asyncio.to_thread(call_gpt)
        except Exception:
            prof["ai_summary"] = (
                f"{prof.get('full_name')}, based in {prof.get('location') or 'Global'}, demonstrates extensive execution parameters."
            )
        return prof

    tasks = [enrich_single(p) for p in profiles]
    import asyncio

    enriched_single_profiles = await asyncio.gather(*tasks)

    # DEBUG: Log emails being sent to the UI
    print(f"\n--- DEBUG: SEARCH RESULTS FOR '{q}' ---")
    for p in enriched_single_profiles:
        print(f"Candidate: {p.get('full_name')} | Email: {p.get('email')}")
    print("------------------------------------------\n")

    # Caching removed as per user request
    return enriched_single_profiles


async def background_scrape_for_query(query: str, location: str | None = None, platform: str | None = None):
    print(f"DEBUG: Starting background automated scraper for query='{query}', location='{location}', platform='{platform}'")
    page_size = 15
    max_pages = 3  # Scrape up to 3 pages in background to populate DB
    
    if platform and platform.lower() in sourcing_service.providers:
        platforms_to_scrape = [platform.lower()]
    else:
        platforms_to_scrape = ["github", "linkedin", "twitter", "stackoverflow", "wellfound"]
        
    import asyncio
    
    for platform_name in platforms_to_scrape:
        provider = sourcing_service.providers.get(platform_name)
        if not provider:
            continue
            
        for page in range(1, max_pages + 1):
            try:
                profiles = await asyncio.to_thread(provider.search, query, location, page, page_size)
                if profiles:
                    import os
                    from pymongo import MongoClient
                    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
                    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "croar_sourcing")
                    mongo_client = MongoClient(MONGO_URI)
                    mongo_db = mongo_client[MONGO_DB_NAME]
                    profiles_collection = mongo_db["candidate_profiles"]
                    
                    for prof in profiles:
                        url = prof.get("profile_url")
                        if url:
                            profiles_collection.update_one(
                                {"profile_url": url}, {"$set": prof}, upsert=True
                            )
                else:
                    break
            except Exception as e:
                print(f"DEBUG: Background scraper error for {platform_name} page {page}: {e}")
                break
    print(f"DEBUG: Background automated scraper completed for query='{query}'")


@router.get("/chat_db")
async def chat_mongodb_profiles(
    q: str = Query(..., description="The chat prompt query"),
    page: int = Query(1, description="Page index"),
    limit: int = Query(10, description="Items per page"),
    background_tasks: BackgroundTasks = None,
):
    """
    Search and summarize matching candidates directly from the local MongoDB store.
    """
    import json
    import os

    from openai import OpenAI
    from pymongo import MongoClient

    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "croar_sourcing")

    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    try:
        try:
            completion = openai_client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[
                    {
                        "role": "system",
                        "content": """You are an AI recruitment database assistant. 
                    Extract explicit structured search conditions from the user's natural language request.
                    Map constraints cleanly into a structured JSON payload:
                    {
                        "role_keywords": ["frontend", "developer"],
                        "platform": "linkedin" or "github" or "devto" or a free-form string or null,
                        "location": "london" or a free-form string or null,
                        "seniority_keywords": ["senior", "lead"]
                    }
                    Only return the strict raw JSON string without markdown wrapping.
                    """,
                    },
                    {"role": "user", "content": f"Extract constraints from: '{q}'"},
                ],
                response_format={"type": "json_object"},
                max_tokens=200,
            )
            gpt_data = json.loads(completion.choices[0].message.content.strip())
        except Exception:
            gpt_data = {"role_keywords": q.lower().split()}

        client = MongoClient(MONGO_URI)
        db = client[MONGO_DB_NAME]
        coll = db["candidate_profiles"]

        keyword_clauses = []

        combined_keys = gpt_data.get("role_keywords", []) + gpt_data.get("seniority_keywords", [])
        if combined_keys:
            for k in combined_keys:
                if len(k) > 2:
                    keyword_clauses.append({"headline": {"$regex": k, "$options": "i"}})
                    keyword_clauses.append({"skills": {"$regex": k, "$options": "i"}})
                    keyword_clauses.append({"full_name": {"$regex": k, "$options": "i"}})

        and_clauses = []
        if keyword_clauses:
            and_clauses.append({"$or": keyword_clauses})

        plat = gpt_data.get("platform")
        if plat:
            and_clauses.append({"platform": {"$regex": plat, "$options": "i"}})

        loc = gpt_data.get("location")
        if loc:
            and_clauses.append({"location": {"$regex": loc, "$options": "i"}})

        query_filter = {"$and": and_clauses} if and_clauses else {}
        total_count = coll.count_documents(query_filter)

        # Sort by Email existence first, then by newest
        skip_amount = (page - 1) * limit
        cursor = coll.find(query_filter).sort([("email", -1), ("_id", -1)]).skip(skip_amount).limit(limit)

        profiles = []
        for doc in cursor:
            if "_id" in doc:
                doc.pop("_id")
            profiles.append(doc)

        if not profiles:
            # Determine platform(s) to search for background scraper
            target_platform = gpt_data.get("platform")
            location_str = gpt_data.get("location")
            search_query = " ".join(
                gpt_data.get("role_keywords", []) + gpt_data.get("seniority_keywords", [])
            )
            if not search_query.strip():
                search_query = q

            # Trigger background scraper to ingest more candidates in parallel
            if background_tasks and search_query:
                background_tasks.add_task(
                    background_scrape_for_query, 
                    search_query, 
                    location_str, 
                    target_platform
                )

            return {
                "response": "No matching profiles indexed\nTrigger background automated scrapers or loosen standard keyword bindings.",
                "profiles": [],
            }

        import asyncio
        import os

        from openai import OpenAI

        openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        async def generate_summary(prof):
            try:

                def call_gpt():
                    # Include email in the context if it exists
                    contact_info = (
                        f"Email: {prof.get('email')}" if prof.get("email") else "Contact: Not available"
                    )
                    completion = openai_client.chat.completions.create(
                        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                        messages=[
                            {
                                "role": "system",
                                "content": "You are an AI recruitment consultant. Write a highly professional, engaging 1-2 sentence assessment. If an email is provided, mention that a direct contact is available.",
                            },
                            {
                                "role": "user",
                                "content": f"Name: {prof.get('full_name')}\nHeadline: {prof.get('headline')}\nLocation: {prof.get('location')}\nSkills: {prof.get('skills')}\n{contact_info}",
                            },
                        ],
                        max_tokens=150,
                    )
                    return completion.choices[0].message.content.strip()

                prof["ai_summary"] = await asyncio.to_thread(call_gpt)
            except Exception:
                prof["ai_summary"] = (
                    f"{prof.get('full_name')} is an accomplished professional recognized for strong execution parameters across modern engineering environments."
                )
            return prof

        enrichment_tasks = [generate_summary(p) for p in profiles]
        summarized_profiles = await asyncio.gather(*enrichment_tasks)

        response_msg = f"I queried the database clusters and flagged {total_count} matching profiles. Here are the most recent matches including those with direct contact info."

        return {"response": response_msg, "profiles": summarized_profiles, "total_count": total_count}
    except Exception as e:
        return {"response": f"Localized database evaluation issue: {e}", "profiles": []}


@router.get("/chat_distribution")
async def get_chat_distribution(q: str = Query(..., description="The chat prompt query")):
    """
    Returns the full location distribution for a search query.
    """
    import json
    import os

    from openai import OpenAI
    from pymongo import MongoClient

    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "croar_sourcing")

    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    try:
        try:
            completion = openai_client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[
                    {
                        "role": "system",
                        "content": """Extract search conditions:
                    {
                        "role_keywords": ["frontend", "developer"],
                        "platform": "linkedin" or "github" or "devto" or null,
                        "location": "london" or null,
                        "seniority_keywords": ["senior", "lead"]
                    }
                    """,
                    },
                    {"role": "user", "content": f"Extract constraints from: '{q}'"},
                ],
                response_format={"type": "json_object"},
                max_tokens=200,
            )
            gpt_data = json.loads(completion.choices[0].message.content.strip())
        except Exception:
            gpt_data = {"role_keywords": q.lower().split()}

        client = MongoClient(MONGO_URI)
        db = client[MONGO_DB_NAME]
        coll = db["candidate_profiles"]

        keyword_clauses = []
        combined_keys = gpt_data.get("role_keywords", []) + gpt_data.get("seniority_keywords", [])
        if combined_keys:
            for k in combined_keys:
                if len(k) > 2:
                    keyword_clauses.append({"headline": {"$regex": k, "$options": "i"}})
                    keyword_clauses.append({"skills": {"$regex": k, "$options": "i"}})
                    keyword_clauses.append({"full_name": {"$regex": k, "$options": "i"}})

        and_clauses = []
        if keyword_clauses:
            and_clauses.append({"$or": keyword_clauses})
        plat = gpt_data.get("platform")
        if plat:
            and_clauses.append({"platform": {"$regex": plat, "$options": "i"}})
        loc_filter = gpt_data.get("location")
        if loc_filter:
            and_clauses.append({"location": {"$regex": loc_filter, "$options": "i"}})

        query_filter = {"$and": and_clauses} if and_clauses else {}

        pipeline = [
            {"$match": query_filter},
            {"$group": {"_id": "$location", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]

        distribution = list(coll.aggregate(pipeline))
        return [{"location": d["_id"], "count": d["count"]} for d in distribution]
    except Exception as e:
        print(f"Error in distribution: {e}")
        return []


@router.get("/profile_details")
async def get_profile_details(url: str = Query(..., description="The direct profile URL")):
    """
    Scrapes rich public details from the direct profile URL.
    """
    import os

    import requests
    from bs4 import BeautifulSoup

    # Normalize relative paths to full URLs (defaulting to Kaggle for /username format)
    if not url.startswith("http"):
        if url.startswith("/"):
            url = f"https://www.kaggle.com{url}"
        else:
            url = f"https://{url}"

    # Normalize localized LinkedIn domains to standard global domain
    if "linkedin.com" in url:
        import re

        url = re.sub(r"https?://[a-z]{2,3}\.linkedin\.com", "https://www.linkedin.com", url)

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
                            disc_soup = BeautifulSoup(disc_html, "html.parser")
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
                    except:
                        raise e

        if not html_content:
            return {"error": "Failed to extract readable public profile details after retries."}

        html = html_content
        if not html:
            return {"error": "Empty HTML content received"}

        soup = BeautifulSoup(html, "html.parser")

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
                gl_lines = [l for l in gl_lines if l]
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
                ka_lines = [l for l in ka_lines if l]
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
                hr_lines = [l for l in hr_lines if l]
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
                lc_lines = [l for l in lc_lines if l]
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
                ph_lines = [l for l in ph_lines if l]
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
                tw_lines = [l for l in tw_lines if l]
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
                wf_lines = [l for l in wf_lines if l]
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
                dr_lines = [l for l in dr_lines if l]
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
