"""Lightweight skill matching between a candidate and a job.

Used to (a) rank Candidate-Bank people by fit to a job (job detail "Candidate Bank" tab) and
(b) rank a company's jobs by fit to a candidate (Candidate Bank "invite to a role"). Case-insensitive
set overlap on the skill lists — cheap, deterministic, no LLM needed.
"""

from __future__ import annotations

from typing import Any


def _norm(skills: Any) -> set[str]:
    if not skills:
        return set()
    return {str(s).strip().lower() for s in skills if s and str(s).strip()}


def overlap(candidate_skills: Any, job_skills: Any) -> tuple[int, list[str], float]:
    """Return (match_count, matched_skill_labels, match_pct).

    `match_pct` is the share of the JOB's required skills the candidate has (0-100), so a candidate
    who covers more of what the role needs ranks higher regardless of how many extra skills they list.
    Original-cased job labels are returned for display.
    """
    cs = _norm(candidate_skills)
    if not cs:
        return 0, [], 0.0
    job_list = [str(s).strip() for s in (job_skills or []) if s and str(s).strip()]
    js_lower = {s.lower(): s for s in job_list}
    matched_lower = cs & set(js_lower.keys())
    matched_labels = [js_lower[m] for m in sorted(matched_lower)]
    pct = round(100.0 * len(matched_labels) / len(job_list), 1) if job_list else 0.0
    return len(matched_labels), matched_labels, pct
