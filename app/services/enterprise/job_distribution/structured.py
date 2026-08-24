"""Standards-based job syndication artifacts.

Two portable, non-per-portal mechanisms power real distribution:

1. ``build_job_posting_jsonld`` — a schema.org/JobPosting JSON-LD object rendered on the
   public job page. Google for Jobs, Indeed, and the Japanese aggregators 求人ボックス
   (Kyujin Box) and スタンバイ (Stanby) all crawl this. This is the free, portable baseline
   that reaches both Korea and Japan.
   Ref: https://developers.google.com/search/docs/appearance/structured-data/job-posting

2. ``build_indeed_feed_xml`` — an Indeed Job Sync XML feed (``<source>``/``<job>``) that
   Indeed ingests. Ref: https://docs.indeed.com/job-sync-xml/xml-feed

Both are derived purely from the job's own data — no external calls.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

# `escape` only entity-encodes our OWN output (&, <, >) when building the Indeed XML feed —
# it does not parse untrusted XML, so the XXE class of attack B406 warns about does not apply.
from xml.sax.saxutils import escape  # nosec B406


def _utcnow() -> datetime:
    """The naive UTC timestamp datetime.utcnow() used to return, without the deprecated call.

    Naive on purpose: these values sit alongside job.created_at (a naive DateTime column) and
    are rendered with .isoformat(). An aware datetime would append a '+00:00' offset for the
    fallback path only, making the emitted feed inconsistent with the created_at path.
    """
    return datetime.now(UTC).replace(tzinfo=None)


# job_type free-text -> schema.org employmentType enum
_EMPLOYMENT_TYPE = {
    "full-time": "FULL_TIME",
    "fulltime": "FULL_TIME",
    "full time": "FULL_TIME",
    "part-time": "PART_TIME",
    "part time": "PART_TIME",
    "contract": "CONTRACTOR",
    "contractor": "CONTRACTOR",
    "temporary": "TEMPORARY",
    "temp": "TEMPORARY",
    "internship": "INTERN",
    "intern": "INTERN",
    "volunteer": "VOLUNTEER",
    "freelance": "CONTRACTOR",
}

# work_mode free-text that means "remote"
_REMOTE_MODES = {"remote", "fully remote", "work from home", "wfh", "telecommute"}


def _employment_type(job: Any) -> str:
    return _EMPLOYMENT_TYPE.get((getattr(job, "job_type", None) or "").strip().lower(), "FULL_TIME")


def _is_remote(job: Any) -> bool:
    return (getattr(job, "work_mode", None) or "").strip().lower() in _REMOTE_MODES


def _date_posted(job: Any) -> str:
    dt = getattr(job, "created_at", None)
    if isinstance(dt, datetime):
        return dt.date().isoformat()
    return _utcnow().date().isoformat()


def _valid_through(job: Any) -> str:
    """Google requires an expiry to keep jobs fresh; default 60 days from posting."""
    dt = getattr(job, "created_at", None)
    base = dt if isinstance(dt, datetime) else _utcnow()
    return (base + timedelta(days=60)).replace(microsecond=0).isoformat()


def _split_location(location: str | None) -> tuple[str, str, str]:
    """Best-effort split of a free-text "City, Region, Country" into (city, region, country)."""
    if not location:
        return "", "", ""
    parts = [p.strip() for p in location.split(",") if p.strip()]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[-1]
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return parts[0], "", ""


def build_job_posting_jsonld(job: Any, company: Any | None, job_url: str) -> dict[str, Any]:
    """Return a schema.org/JobPosting dict ready to serialize as JSON-LD."""
    city, region, country = _split_location(getattr(job, "location", None))
    org_name = getattr(company, "name", None) or "Company"

    data: dict[str, Any] = {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": getattr(job, "title", "") or "",
        "description": getattr(job, "description", "") or "",
        "datePosted": _date_posted(job),
        "validThrough": _valid_through(job),
        "employmentType": _employment_type(job),
        "identifier": {"@type": "PropertyValue", "name": org_name, "value": str(getattr(job, "id", ""))},
        "hiringOrganization": {"@type": "Organization", "name": org_name},
        "directApply": True,
        "url": job_url,
    }

    website = getattr(company, "website", None) if company else None
    if website:
        data["hiringOrganization"]["sameAs"] = website
    logo = getattr(company, "logo_url", None) if company else None
    if logo:
        data["hiringOrganization"]["logo"] = logo

    # Location — either a physical place or remote (TELECOMMUTE). Google requires one of
    # jobLocation / jobLocationType, so fall back to the company's country when the job has
    # no explicit location.
    if not country and company is not None:
        country = getattr(company, "country", None) or ""
    if _is_remote(job):
        data["jobLocationType"] = "TELECOMMUTE"
        if country:
            data["applicantLocationRequirements"] = {"@type": "Country", "name": country}
    if city or region or country:
        data["jobLocation"] = {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": city,
                "addressRegion": region,
                "addressCountry": country or "",
            },
        }
    elif "jobLocationType" not in data:
        # No location at all — mark remote so the markup stays valid.
        data["jobLocationType"] = "TELECOMMUTE"

    # Salary (recommended).
    smin = getattr(job, "salary_min", None)
    smax = getattr(job, "salary_max", None)
    if smin or smax:
        unit = (getattr(job, "salary_frequency", None) or "YEAR").upper()
        unit = {"YEARLY": "YEAR", "MONTHLY": "MONTH", "HOURLY": "HOUR", "WEEKLY": "WEEK"}.get(unit, unit)
        value: dict[str, Any] = {"@type": "QuantitativeValue", "unitText": unit}
        if smin and smax:
            value["minValue"] = float(smin)
            value["maxValue"] = float(smax)
        else:
            value["value"] = float(smin or smax)
        data["baseSalary"] = {
            "@type": "MonetaryAmount",
            "currency": getattr(job, "salary_currency", None) or "USD",
            "value": value,
        }

    skills = getattr(job, "required_skills", None)
    if skills:
        data["skills"] = ", ".join(skills)

    return data


def _el(tag: str, value: Any) -> str:
    if value is None or value == "":
        return ""
    return f"<{tag}>{escape(str(value))}</{tag}>"


def _cdata(tag: str, value: Any) -> str:
    if not value:
        return f"<{tag}></{tag}>"
    text = str(value).replace("]]>", "]]&gt;")
    return f"<{tag}><![CDATA[{text}]]></{tag}>"


def build_indeed_job_node(job: Any, company: Any | None, job_url: str, apply_email: str) -> str:
    """One <job> node for the Indeed Job Sync XML feed."""
    city, region, country = _split_location(getattr(job, "location", None))
    org_name = getattr(company, "name", None) or "Company"
    posted = getattr(job, "created_at", None)
    date_str = posted.strftime("%a, %d %b %Y %H:%M:%S GMT") if isinstance(posted, datetime) else ""
    remote = "Fully remote" if _is_remote(job) else ""

    parts = [
        "<job>",
        _cdata("title", getattr(job, "title", "")),
        _el("date", date_str),
        _el("referencenumber", getattr(job, "id", "")),
        _el("requisitionid", getattr(job, "id", "")),
        _cdata("url", job_url),
        _cdata("company", org_name),
        _cdata("city", city),
        _cdata("state", region),
        _cdata("country", country),
        _el("email", apply_email),
        _cdata("description", getattr(job, "description", "")),
        _el("jobtype", _employment_type(job).lower().replace("_", "")),
    ]
    if remote:
        parts.append(_el("remotetype", remote))
    smin = getattr(job, "salary_min", None)
    smax = getattr(job, "salary_max", None)
    if smin or smax:
        cur = getattr(job, "salary_currency", None) or ""
        parts.append(_cdata("salary", f"{cur} {smin or ''}-{smax or ''}".strip()))
    parts.append("</job>")
    return "".join(p for p in parts if p)


def build_indeed_feed_xml(jobs: list[tuple[Any, Any, str]], publisher: str, apply_email: str) -> str:
    """Full Indeed Job Sync XML feed. ``jobs`` = list of (job, company, job_url)."""
    nodes = "".join(build_indeed_job_node(j, c, u, apply_email) for j, c, u in jobs)
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<source>"
        f"{_el('publisher', publisher)}"
        f"{_el('publisherurl', '')}"
        f"{_el('lastBuildDate', _utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT'))}"
        f"{nodes}"
        "</source>"
    )
