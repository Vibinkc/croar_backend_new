"""Applications that arrive as email.

Most job boards will not let an ATS pull applications out of them, but nearly all of them will
*forward* each applicant to an address you nominate. That is the whole mechanism here: every job
gets its own address, you point the board's job ad at it, and anything that lands there becomes a
candidate on that job — CV parsed, scored against the description, deduplicated by email.

The same address doubles as a manual route: forward a CV to it and the person appears on the job.

Two deliberate choices:

* The address is a plus-address on a mailbox that already exists (the company's connected mailbox
  if it has one, otherwise the platform's), so no DNS or mail-server work is needed to start
  using it. Mailboxes that do not support plus-addressing simply never receive anything, which
  the UI says rather than discovering silently.
* The job is identified by an HMAC of its id, not the id itself. A raw uuid in a public address
  invites someone to walk the range and post candidates into jobs that are not theirs.
"""

from __future__ import annotations

import email
import hmac
import imaplib
import os
import re
import uuid as _uuid
from hashlib import sha256
from typing import TYPE_CHECKING, Any, cast

import aiofiles
from loguru import logger
from sqlalchemy import func, select

from app.core.settings import get_settings
from app.models.enterprise.candidate import Candidate, CandidateApplication

if TYPE_CHECKING:  # annotations only — this module never constructs either
    from email.message import Message

    from sqlalchemy.ext.asyncio import AsyncSession

_settings = get_settings()

RESUME_DIR = "uploads/resumes"
MAX_RESUME_BYTES = 20 * 1024 * 1024
ALLOWED_RESUME_EXTS = {".pdf", ".doc", ".docx", ".rtf", ".txt"}

# How many unread messages one check will look at. A board that has been forwarding for months
# should not turn a single click into a thousand AI calls.
MAX_MESSAGES_PER_CHECK = 25


class CvParseError(Exception):
    """The CV could not be turned into a candidate. Carries a message fit for a human."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message = message
        self.status = status


# ── the per-job address ───────────────────────────────────────────────────────────────────────


def job_token(job_id: Any) -> str:
    """A short, stable, unguessable tag for a job.

    HMAC rather than a truncated uuid: the address ends up in a job ad on a public board, and
    anything derived reversibly from the id lets a stranger address other people's jobs.
    """
    mac = hmac.new(_settings.secret_key.encode(), str(job_id).encode(), sha256)
    return mac.hexdigest()[:12]


def address_for(job_id: Any, mailbox: str | None) -> str | None:
    """The plus-address that feeds this job, or None when no mailbox is configured."""
    mailbox = (mailbox or "").strip()
    if "@" not in mailbox:
        return None
    local, _, domain = mailbox.partition("@")
    # A mailbox that is already plus-addressed would otherwise produce two tags.
    local = local.split("+", 1)[0]
    return f"{local}+job-{job_token(job_id)}@{domain}"


_TOKEN_RE = re.compile(r"\+job-([0-9a-f]{12})@", re.I)


def token_in(text: str | None) -> str | None:
    """Pull a job tag out of a header. Handles the several places a forward can put it."""
    if not text:
        return None
    m = _TOKEN_RE.search(text)
    return m.group(1).lower() if m else None


def token_for_message(msg: Message) -> str | None:
    """Find the job tag anywhere the delivery path may have recorded it.

    A forwarded application often has the original address only in Delivered-To or
    X-Original-To, because the visible To: is whatever the board put there.
    """
    for header in ("Delivered-To", "X-Original-To", "To", "Cc", "X-Forwarded-To", "Envelope-To"):
        for value in msg.get_all(header, []):
            found = token_in(str(value))
            if found:
                return found
    return None


# ── reading the CV ────────────────────────────────────────────────────────────────────────────


def resume_text(content: bytes) -> str:
    """Pull text out of a CV, falling back to a raw decode for non-PDFs."""
    import contextlib

    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(content)
        parts = [pdf.get_page(i).get_textpage().get_text_range() for i in range(len(pdf))]
        text = "\n".join(parts).strip()
        if text:
            return text
    except Exception:
        pass
    with contextlib.suppress(Exception):
        return content.decode("utf-8", errors="ignore")
    return ""


def safe_name(raw: str) -> str:
    """Reduce a filename to a harmless basename — an emailed attachment names itself."""
    base = os.path.basename(str(raw or "")).strip() or "resume.pdf"
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)[:120]


async def create_candidate_from_cv(
    session: AsyncSession, job: Any, filename: str, data: bytes, source: str
) -> dict[str, Any]:
    """Parse a CV, score it against the job, and put the person on that job.

    Shared by the recruiter's upload button and the email ingester so both produce the same
    record: one person per email address per company, one application per (person, job).
    """
    import json

    from app.core.ai import analyze_text_with_llm

    original = safe_name(filename)
    ext = os.path.splitext(original)[1].lower()
    if ext and ext not in ALLOWED_RESUME_EXTS:
        raise CvParseError(f"{ext} is not a supported CV format", 400)
    if not data:
        raise CvParseError("File is empty", 400)
    if len(data) > MAX_RESUME_BYTES:
        raise CvParseError("CV is larger than 20 MB", 413)

    text = resume_text(data)
    if not text.strip():
        raise CvParseError("No text could be read from that CV. A scanned image needs OCR before upload.")

    prompt = f"""
    You are an expert recruiter and ATS system.

    Extract the candidate's details from the RESUME TEXT and score them against the JOB.

    JOB TITLE: {job.title}
    JOB DESCRIPTION: {(job.description or "")[:4000]}
    REQUIRED SKILLS: {", ".join(job.required_skills or [])}

    RESUME TEXT:
    {text[:12000]}

    OUTPUT JSON FORMAT:
    {{
        "candidate_details": {{
            "full_name": "...",
            "email": "...",
            "phone": "...",
            "location": "...",
            "total_experience": 0,
            "skills": ["..."]
        }},
        "analysis": {{ "score": 85, "fit_reason": "...", "not_fit_reason": "...", "highlights": ["..."] }}
    }}
    """
    try:
        parsed = json.loads(await analyze_text_with_llm(prompt))
    except Exception as e:
        # The AI is the whole point here; a stub candidate called "Candidate" with no email is
        # worse than telling the caller it failed.
        raise CvParseError(f"Could not read that CV with AI: {e!s}", 502) from e

    details = parsed.get("candidate_details") or {}
    analysis = parsed.get("analysis") or {}
    email_addr = (details.get("email") or "").strip().lower()
    full_name = (details.get("full_name") or "").strip() or "Candidate"

    # Keep the CV so the profile can open it later. Best-effort: losing the file must not lose
    # the candidate.
    resume_path = None
    try:
        os.makedirs(RESUME_DIR, exist_ok=True)
        stored = f"{_uuid.uuid4().hex[:8]}_{original}"
        # aiofiles, not open(): this runs while polling a mailbox, so a slow disk here would
        # block the event loop for every other request.
        async with aiofiles.open(os.path.join(RESUME_DIR, stored), "wb") as fh:
            await fh.write(data)
        resume_path = f"{RESUME_DIR}/{stored}"
    except OSError:
        pass

    # Re-use an existing person rather than creating a duplicate — matching on email within the
    # company. This is the same "one person, one record" rule the search-first UI is built on.
    candidate = None
    if email_addr:
        candidate = (
            await session.execute(
                select(Candidate).where(
                    Candidate.email == email_addr,
                    Candidate.company_id == job.company_id,
                    Candidate.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    created_candidate = False
    if candidate:
        # Fill gaps on the existing record; never overwrite what a human may have corrected.
        if resume_path and not candidate.resume_file_path:
            candidate.resume_file_path = resume_path
        if not candidate.skills and details.get("skills"):
            candidate.skills = [str(s) for s in details["skills"]][:40]
    else:
        candidate = Candidate(
            full_name=full_name,
            email=email_addr or None,
            phone=(details.get("phone") or None),
            skills=[str(s) for s in (details.get("skills") or [])][:40],
            resume_file_path=resume_path,
            company_id=job.company_id,
            source_platform=source,
        )
        session.add(candidate)
        await session.flush()
        created_candidate = True

    existing = (
        await session.execute(
            select(CandidateApplication).where(
                CandidateApplication.candidate_id == candidate.id,
                CandidateApplication.job_requirement_id == job.id,
                CandidateApplication.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    if existing:
        application_id = existing.id
        already = True
    else:
        try:
            score = float(analysis.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        application = CandidateApplication(
            candidate_id=candidate.id,
            job_requirement_id=job.id,
            status_id=1,
            current_stage=1,
            source=source,
            ai_match_score=score,
            ai_feedback=analysis,
            company_id=job.company_id,
            applied_at=cast("Any", func.now()),
        )
        session.add(application)
        await session.flush()
        application_id = application.id
        already = False

    return {
        "application_id": str(application_id),
        "candidate_id": str(candidate.id),
        "full_name": full_name,
        "email": email_addr,
        "match_score": analysis.get("score"),
        "created_candidate": created_candidate,
        "already_on_job": already,
    }


# ── reading the mailbox ───────────────────────────────────────────────────────────────────────


def _attachments(msg: Message) -> list[tuple[str, bytes]]:
    """Every attachment that looks like a CV, largest first.

    Signature images and tracking pixels arrive as attachments too, so extension is the filter
    and size is the tie-break — a real CV is the biggest document in the message.
    """
    found: list[tuple[str, bytes]] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        name = part.get_filename()
        if not name:
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED_RESUME_EXTS:
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:  # nosec B112 - a malformed part is skipped, not fatal to the message
            continue
        if payload:
            found.append((name, payload))
    found.sort(key=lambda p: len(p[1]), reverse=True)
    return found


def _body_text(msg: Message) -> str:
    """The message body, for boards that paste the CV inline instead of attaching it."""
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                payload = part.get_payload(decode=True)
            except Exception:  # nosec B112 - as above: skip the part, keep reading the message
                continue
            if payload:
                return payload.decode("utf-8", errors="ignore")
    return ""


def _imap_settings(conn: dict[str, Any] | None) -> tuple[str, int, str, str, str] | None:
    """(host, port, user, password, mailbox_address) for the company's mailbox, else the platform's."""
    if conn:
        host = str(conn.get("imap_host") or "")
        user = str(conn.get("username") or conn.get("email") or "")
        pw = str(conn.get("password") or "")
        addr = str(conn.get("email") or user)
        if host and user and pw:
            return host, int(conn.get("imap_port") or 993), user, pw, addr
    host = str(_settings.imap_address or "")
    user = str(_settings.imap_username or "")
    pw = str(_settings.imap_password or "")
    if host and user and pw:
        return host, int(_settings.imap_port or 993), user, pw, user
    return None


def mailbox_address(conn: dict[str, Any] | None) -> str | None:
    """Which mailbox this company's job addresses hang off, if any is usable."""
    resolved = _imap_settings(conn)
    return resolved[4] if resolved else None


def _fetch_unseen(settings_tuple: tuple[str, int, str, str, str], limit: int) -> list[Message]:
    """Read unseen messages and mark them seen, so a second check does not re-import them."""
    host, port, user, pw, _addr = settings_tuple
    msgs: list[Message] = []
    with imaplib.IMAP4_SSL(host, port) as imap:
        imap.login(user, pw)
        imap.select("INBOX")
        typ, data = imap.search(None, "UNSEEN")
        if typ != "OK" or not data or not data[0]:
            return msgs
        ids = data[0].split()[-limit:]
        for mid in ids:
            typ, raw = imap.fetch(mid, "(RFC822)")
            if typ != "OK" or not raw or not isinstance(raw[0], tuple):
                continue
            msgs.append(email.message_from_bytes(raw[0][1]))
            # Only mark what we actually read; a fetch that failed should be retried next time.
            imap.store(mid, "+FLAGS", "\\Seen")
    return msgs


async def check_job_inbox(session: AsyncSession, job: Any, conn: dict[str, Any] | None) -> dict[str, Any]:
    """Import any waiting applications for one job.

    Returns counts rather than raising: a mailbox that is unreachable, or a CV the AI could not
    read, is something the recruiter needs told, not a 500.
    """
    resolved = _imap_settings(conn)
    if not resolved:
        return {
            "ok": False,
            "created": 0,
            "checked": 0,
            "message": "No mailbox is connected, so there is nowhere for applications to arrive.",
        }

    want = job_token(job.id)
    try:
        messages = _fetch_unseen(resolved, MAX_MESSAGES_PER_CHECK)
    except Exception as e:
        logger.warning(f"job inbox: could not read mailbox for job {job.id}: {e}")
        return {"ok": False, "created": 0, "checked": 0, "message": f"Could not read the mailbox: {e!s}"}

    created: list[dict[str, Any]] = []
    skipped: list[str] = []
    checked = 0
    for msg in messages:
        if token_for_message(msg) != want:
            continue
        checked += 1
        parts = _attachments(msg)
        if not parts:
            # Some boards paste the application into the body. Treat that as the CV text.
            body = _body_text(msg)
            if not body.strip():
                skipped.append(f"{msg.get('Subject') or 'A message'}: no CV attached")
                continue
            parts = [("application.txt", body.encode("utf-8"))]
        name, data = parts[0]
        try:
            result = await create_candidate_from_cv(session, job, name, data, "Job board email")
            created.append(result)
        except CvParseError as e:
            skipped.append(f"{msg.get('Subject') or 'A message'}: {e.message}")
        except Exception as e:  # one bad message must not abandon the rest
            logger.warning(f"job inbox: {job.id} failed on a message: {e}")
            skipped.append(f"{msg.get('Subject') or 'A message'}: {e!s}")

    if created:
        await session.commit()

    return {
        "ok": True,
        "created": len(created),
        "checked": checked,
        "candidates": created,
        "skipped": skipped,
        "message": (
            f"Added {len(created)} candidate(s) from {checked} message(s)."
            if created
            else "Nothing new addressed to this job."
        ),
    }
