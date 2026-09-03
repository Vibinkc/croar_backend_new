"""Walk a brand-new account through the whole hiring flow and report what works.

Drives the real HTTP API rather than the ORM, so what this proves is what a browser would get:
permissions, validation, serialisation and all. One fresh company per run, so nothing depends
on data an earlier session left behind — a check that only passes on a seeded demo account is
not a check.

Every step records PASS / FAIL / SKIP with the status code and, on failure, what the server
said. A step that cannot run because an earlier one failed is SKIP, not FAIL: reporting six
cascading failures for one broken endpoint hides which one to fix.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from typing import Any

import httpx

BASE = "http://localhost:8000/api/v1"
TIMEOUT = 90.0

results: list[dict] = []
ctx: dict = {}


def record(module: str, name: str, status: str, code: int | None = None, detail: str = "") -> None:
    results.append({"module": module, "name": name, "status": status, "code": code, "detail": detail[:300]})
    mark = {"PASS": "ok  ", "FAIL": "FAIL", "SKIP": "skip"}[status]
    line = f"  [{mark}] {name}" + (f" ({code})" if code else "")
    if status == "FAIL" and detail:
        line += f"\n         -> {detail[:200]}"
    print(line, flush=True)


class Api:
    def __init__(self) -> None:
        self.c = httpx.Client(base_url=BASE, timeout=TIMEOUT)
        self.token: str | None = None

    def h(self, extra: dict | None = None) -> dict:
        d = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        d.update(extra or {})
        return d

    def call(
        self,
        module: str,
        name: str,
        method: str,
        path: str,
        *,
        ok: tuple[int, ...] = (200, 201, 204),
        **kw: Any,
    ) -> tuple[Any, Any]:
        """One request, recorded. Returns (response|None, parsed_json|None)."""
        try:
            r = self.c.request(method, path, headers=self.h(kw.pop("headers", None)), **kw)
        except Exception as e:
            record(module, name, "FAIL", None, f"request error: {e}")
            return None, None
        body = None
        try:
            body = r.json()
        except Exception:
            body = r.text[:200]
        if r.status_code in ok:
            record(module, name, "PASS", r.status_code)
            return r, body
        record(module, name, "FAIL", r.status_code, json.dumps(body) if not isinstance(body, str) else body)
        return r, body


api = Api()


def section(title: str) -> None:
    print(f"\n=== {title} ", flush=True)


# ── 1. a brand new account ────────────────────────────────────────────────────────────────────
def signup() -> bool:
    section("Account")
    stamp = uuid.uuid4().hex[:8]
    ctx["email"] = f"qa.{stamp}@croar-e2e.test"
    ctx["password"] = "QaWalk!2026x"
    ctx["company"] = f"QA Walk {stamp}"

    _, body = api.call(
        "Account",
        "sign up (new company)",
        "POST",
        "/auth/signup",
        json={
            "email": ctx["email"],
            "password": ctx["password"],
            "first_name": "Qa",
            "last_name": "Walker",
            "company_name": ctx["company"],
        },
    )
    if not isinstance(body, dict) or not body.get("company_id"):
        return False
    ctx["company_id"] = body["company_id"]

    _, tok = api.call(
        "Account",
        "log in",
        "POST",
        "/auth/token",
        data={"username": ctx["email"], "password": ctx["password"]},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if not isinstance(tok, dict) or not tok.get("access_token"):
        return False
    api.token = tok["access_token"]

    _, me = api.call("Account", "read own profile (/auth/me)", "GET", "/auth/me")
    if isinstance(me, dict):
        ctx["user_id"] = me.get("id")
    return True


# ── 2. company + career page ──────────────────────────────────────────────────────────────────
def company_and_career_page() -> None:
    section("Company & career page")
    _, body = api.call("Company", "read company", "GET", "/enterprise/company/")
    company = body[0] if isinstance(body, list) and body else body
    if isinstance(company, dict):
        ctx["slug"] = company.get("slug")
        api.call(
            "Company",
            "update company (PATCH)",
            "PATCH",
            f"/enterprise/company/{company['id']}",
            json={"industry": "Software", "location": "Remote"},
        )

    api.call("Career page", "read settings", "GET", "/enterprise/career-page/settings")
    api.call(
        "Career page",
        "save settings (PUT)",
        "PUT",
        "/enterprise/career-page/settings",
        json={
            "headline": "Careers at QA Walk",
            "intro": "We are hiring.",
            "brand_color": "#0E8A6E",
            "contact_email": "careers@qa.test",
            "website": "https://qa.test",
            "show_share_buttons": True,
            "application_terms": "Terms.",
            "privacy_policy": "Policy.",
        },
    )
    _, cp2 = api.call("Career page", "settings persisted", "GET", "/enterprise/career-page/settings")
    if isinstance(cp2, dict) and cp2.get("settings", {}).get("headline") == "Careers at QA Walk":
        record("Career page", "round-trip verified", "PASS", 200)
    else:
        record("Career page", "round-trip verified", "FAIL", None, f"got {json.dumps(cp2)[:200]}")

    if ctx.get("slug"):
        api.call(
            "Career page",
            "public branding (no auth)",
            "GET",
            "/enterprise/public/jobs/career-page",
            params={"company_slug": ctx["slug"]},
        )
        api.call(
            "Career page",
            "public job list (no auth)",
            "GET",
            "/enterprise/public/jobs/list",
            params={"company_slug": ctx["slug"]},
        )


# ── 3. jobs ───────────────────────────────────────────────────────────────────────────────────
def jobs() -> None:
    section("Jobs")
    _, body = api.call(
        "Jobs",
        "create job",
        "POST",
        "/enterprise/jobs/",
        json={
            "title": "Senior Backend Engineer",
            "description": "<p>Build and run the services behind our product.</p>",
            "required_skills": ["Python", "PostgreSQL"],
            "experience_min": 4,
            "experience_max": 8,
            "location": "Remote",
            "job_type": "FULL_TIME",
            "work_mode": "REMOTE",
            "salary_min": 100000,
            "salary_max": 150000,
            "salary_currency": "USD",
            "headcount": 2,
            "status_id": 2,
        },
    )
    if not isinstance(body, dict) or not body.get("id"):
        record("Jobs", "everything downstream of a job", "SKIP", None, "no job to work with")
        return
    ctx["job_id"] = body["id"]
    record("Jobs", f"job id {body['id'][:8]}…", "PASS", 200)

    api.call("Jobs", "list jobs", "GET", "/enterprise/jobs/")
    _, one = api.call("Jobs", "read job", "GET", f"/enterprise/jobs/{ctx['job_id']}")
    if isinstance(one, dict):
        ctx["accepting"] = one.get("accepting_applications")
        record("Jobs", f"accepting_applications = {ctx['accepting']}", "PASS", 200)

    api.call(
        "Jobs",
        "update job (PATCH)",
        "PATCH",
        f"/enterprise/jobs/{ctx['job_id']}",
        json={"location": "Remote (EU)"},
    )
    api.call("Jobs", "job activity log", "GET", f"/enterprise/jobs/{ctx['job_id']}/activity")

    # Notes + attachments CRUD
    _, note = api.call(
        "Jobs",
        "create note",
        "POST",
        f"/enterprise/jobs/{ctx['job_id']}/notes",
        json={"body": "Kickoff call done."},
    )
    api.call("Jobs", "list notes", "GET", f"/enterprise/jobs/{ctx['job_id']}/notes")
    if isinstance(note, dict) and note.get("id"):
        api.call(
            "Jobs",
            "update note",
            "PATCH",
            f"/enterprise/jobs/{ctx['job_id']}/notes/{note['id']}",
            json={"body": "Kickoff done. Scope agreed."},
        )
        api.call("Jobs", "delete note", "DELETE", f"/enterprise/jobs/{ctx['job_id']}/notes/{note['id']}")
    else:
        record("Jobs", "update/delete note", "SKIP", None, "note was not created")


# ── 4. publishing ─────────────────────────────────────────────────────────────────────────────
def publishing() -> None:
    section("Job boards")
    _, cat = api.call("Job boards", "portal catalogue", "GET", "/enterprise/job-portals/catalog")
    if isinstance(cat, dict):
        portals = cat.get("portals") or []
        free = [p for p in portals if not p.get("setup_required") and p.get("integration") != "partner"]
        record("Job boards", f"{len(portals)} boards, {len(free)} free", "PASS", 200)
        ctx["free_portals"] = [p["key"] for p in free[:3]]

    if not ctx.get("job_id"):
        record("Job boards", "publish", "SKIP", None, "no job")
        return

    _, pub = api.call(
        "Job boards",
        "publish to free boards",
        "POST",
        f"/enterprise/jobs/{ctx['job_id']}/publish",
        json={"platforms": ctx.get("free_portals") or ["google_jobs"]},
    )
    if isinstance(pub, dict):
        for res in (pub.get("results") or [])[:4]:
            record("Job boards", f"  {res.get('platform')}: {res.get('status')}", "PASS", 200)
        first = (pub.get("results") or [{}])[0].get("platform")
        if first:
            api.call(
                "Job boards",
                f"unpublish {first}",
                "DELETE",
                f"/enterprise/jobs/{ctx['job_id']}/publish/{first}",
            )

    api.call("Job boards", "job inbox address", "GET", f"/enterprise/jobs/{ctx['job_id']}/inbox")


# ── 5. candidates ─────────────────────────────────────────────────────────────────────────────
def candidates() -> None:
    section("Candidates & pipeline")
    if not ctx.get("job_id"):
        record("Candidates", "everything", "SKIP", None, "no job")
        return

    # There is no admin endpoint that creates a candidate; the routes in are the public apply
    # form and a CV upload. Applying is the real journey, so the walk uses it.
    cv = (
        b"Ada Lovelace\nada.qa@example.com\n+10000000000\n"
        b"Senior Backend Engineer with 6 years of Python and PostgreSQL experience.\n"
    )
    email = f"ada.{uuid.uuid4().hex[:6]}@qa.test"
    _, applied = api.call(
        "Candidates",
        "apply via public form (candidate journey)",
        "POST",
        f"/enterprise/public/jobs/{ctx['job_id']}/apply",
        files={"resume": ("ada.txt", cv, "text/plain")},
        data={"full_name": "Ada Lovelace", "email": email, "phone": "+10000000000"},
    )
    if isinstance(applied, dict):
        ctx["application_id"] = applied.get("application_id") or applied.get("id")
        ctx["candidate_id"] = applied.get("candidate_id")

    # And the recruiter-side route: upload a CV onto the job.
    cv2 = f"Grace Hopper\ngrace.{uuid.uuid4().hex[:6]}@example.com\nPython, PostgreSQL, 9 years.\n".encode()
    _, up = api.call(
        "Candidates",
        "upload CV (recruiter route)",
        "POST",
        f"/enterprise/jobs/{ctx['job_id']}/candidates/upload",
        files={"file": ("grace.txt", cv2, "text/plain")},
    )
    if isinstance(up, dict):
        ctx["candidate_id"] = ctx.get("candidate_id") or up.get("candidate_id")
        ctx["application_id"] = ctx.get("application_id") or up.get("application_id")

    api.call("Candidates", "list candidates", "GET", "/enterprise/candidates/")
    if ctx.get("candidate_id"):
        api.call("Candidates", "read candidate", "GET", f"/enterprise/candidates/{ctx['candidate_id']}")

    api.call(
        "Candidates",
        "applications on job",
        "GET",
        "/enterprise/applications/",
        params={"job_requirement_id": ctx["job_id"]},
    )

    if ctx.get("application_id"):
        aid = ctx["application_id"]
        api.call(
            "Pipeline", "move stage", "PATCH", f"/enterprise/applications/{aid}/stage", json={"new_stage": 2}
        )
        api.call(
            "Pipeline", "drop candidate", "POST", f"/enterprise/jobs/{ctx['job_id']}/applications/{aid}/drop"
        )
        api.call(
            "Pipeline",
            "restore candidate",
            "POST",
            f"/enterprise/jobs/{ctx['job_id']}/applications/{aid}/restore",
        )
    else:
        record("Pipeline", "stage moves", "SKIP", None, "no application")


# ── 6. automations ────────────────────────────────────────────────────────────────────────────
def automations() -> None:
    section("Automations")
    if not ctx.get("job_id"):
        record("Automations", "everything", "SKIP", None, "no job")
        return
    jid = ctx["job_id"]

    _, a = api.call(
        "Assessment",
        "create stage automation",
        "POST",
        "/enterprise/assessment/",
        json={
            "job_requirement_id": jid,
            "stage_index": 1,
            "criteria": "Applied",
            "type": "APTITUDE",
            "topic": "Python",
            "question_count": 5,
            "test_duration": 20,
            "is_enabled": True,
            "auto_move": False,
        },
    )
    api.call("Assessment", "list automations", "GET", "/enterprise/assessment/")
    if isinstance(a, dict) and a.get("id"):
        api.call(
            "Assessment",
            "update automation",
            "PATCH",
            f"/enterprise/assessment/{a['id']}",
            json={"question_count": 8},
        )
        ctx["assessment_automation"] = a["id"]

    _, i = api.call(
        "Interview",
        "create stage automation",
        "POST",
        "/enterprise/interview-automation/",
        json={
            "job_requirement_id": jid,
            "stage_index": 2,
            "criteria": "Assessment passed",
            "start_time": "09:00",
            "end_time": "17:00",
            "daily_limit": 5,
            "is_enabled": True,
        },
    )
    api.call("Interview", "list automations", "GET", "/enterprise/interview-automation/")
    if isinstance(i, dict) and i.get("id"):
        ctx["interview_automation"] = i["id"]

    _, tmpl = api.call(
        "Mail",
        "create email template",
        "POST",
        "/enterprise/communication/templates",
        json={
            "name": "Next step",
            "subject": "Next step for {{candidate_name}}",
            "body": "<p>Hello {{candidate_name}}</p>",
            "category": "GENERAL",
        },
    )
    template_id = tmpl.get("id") if isinstance(tmpl, dict) else None
    _, m = api.call(
        "Mail",
        "create stage automation",
        "POST",
        "/enterprise/automation/mail",
        json={
            "job_requirement_id": jid,
            "stage_index": 1,
            "criteria": "Applied",
            "template_id": template_id,
            "is_enabled": True,
            "is_immediate": True,
        },
    )
    api.call("Mail", "list automations", "GET", "/enterprise/automation/mail")
    if isinstance(m, dict) and m.get("id"):
        ctx["mail_automation"] = m["id"]

    _, o = api.call(
        "Onboarding",
        "create stage automation",
        "POST",
        "/enterprise/onboarding-automation/",
        json={"job_requirement_id": jid, "stage_index": 3, "criteria": "Offer accepted", "is_enabled": True},
    )
    api.call("Onboarding", "list automations", "GET", "/enterprise/onboarding-automation/")
    if isinstance(o, dict) and o.get("id"):
        ctx["onboarding_automation"] = o["id"]


# ── 7. assessment the candidate actually takes ────────────────────────────────────────────────
def assessment_flow() -> None:
    section("Assessment (candidate side)")
    api.call("Assessment", "templates list", "GET", "/enterprise/assessment-templates/")
    _, t = api.call(
        "Assessment",
        "create template",
        "POST",
        "/enterprise/assessment-templates/",
        json={
            "name": "Python basics",
            "type": "APTITUDE",
            "topic": "Python",
            "question_count": 1,
            "test_duration": 20,
            "generated_questions": [
                {
                    "question": "What builds a list inline?",
                    "options": ["A loop", "A comprehension", "A module", "A class"],
                    "answer": "A comprehension",
                }
            ],
        },
    )
    if isinstance(t, dict) and t.get("id"):
        ctx["template_id"] = t["id"]
        api.call("Assessment", "read template", "GET", f"/enterprise/assessment-templates/{t['id']}")
        api.call(
            "Assessment",
            "update template",
            "PATCH",
            f"/enterprise/assessment-templates/{t['id']}",
            json={"test_duration": 25},
        )

    if ctx.get("template_id") and ctx.get("application_id"):
        _, send = api.call(
            "Assessment",
            "send to candidate",
            "POST",
            "/enterprise/assessment-templates/bulk-send",
            json={"template_id": ctx["template_id"], "application_ids": [ctx["application_id"]]},
        )
        if isinstance(send, dict):
            ctx["attempt"] = (send.get("attempts") or [{}])[0].get("id") if send.get("attempts") else None
    else:
        record("Assessment", "send to candidate", "SKIP", None, "no template or application")

    api.call("Assessment", "attempts pending review", "GET", "/enterprise/assessment/attempts/pending-review")


# ── 8. onboarding ─────────────────────────────────────────────────────────────────────────────
def onboarding() -> None:
    section("Onboarding")
    api.call("Onboarding", "statuses", "GET", "/enterprise/onboarding/statuses")
    api.call("Onboarding", "list onboardings", "GET", "/enterprise/onboarding/")
    _, tpl = api.call(
        "Onboarding",
        "create template",
        "POST",
        "/enterprise/onboarding/templates/",
        json={
            "name": "Standard onboarding",
            "documents": [{"name": "ID proof", "required": True}],
            "tasks": [{"title": "Sign contract"}],
        },
    )
    api.call("Onboarding", "list templates", "GET", "/enterprise/onboarding/templates/")

    if ctx.get("application_id"):
        _, ob = api.call(
            "Onboarding",
            "initiate for hired candidate",
            "POST",
            "/enterprise/onboarding/initiate",
            json={
                "application_id": ctx["application_id"],
                "template_id": (tpl or {}).get("id") if isinstance(tpl, dict) else None,
                "start_date": "2026-10-01",
            },
        )
        if isinstance(ob, dict) and ob.get("id"):
            ctx["onboarding_id"] = ob["id"]
            app_obj = ob.get("application") or {}
            ctx["onboarded_candidate_id"] = (app_obj.get("candidate") or {}).get("id") or app_obj.get(
                "candidate_id"
            )
            api.call("Onboarding", "read onboarding back", "GET", f"/enterprise/onboarding/{ob['id']}")
            _, lst = api.call("Onboarding", "appears in the list", "GET", "/enterprise/onboarding/")
            rows = lst if isinstance(lst, list) else []
            if any(str(r.get("id")) == str(ob["id"]) for r in rows):
                record("Onboarding", "persisted (survives the request)", "PASS", 200)
            else:
                record(
                    "Onboarding",
                    "persisted (survives the request)",
                    "FAIL",
                    None,
                    f"created 201 but absent from the list ({len(rows)} rows)",
                )
            api.call(
                "Onboarding",
                "add note",
                "POST",
                f"/enterprise/onboarding/{ob['id']}/notes",
                json={"content": "Offer accepted."},
            )
            api.call(
                "Onboarding",
                "add task",
                "POST",
                f"/enterprise/onboarding/{ob['id']}/tasks",
                json={"title": "Order laptop"},
            )
    else:
        record("Onboarding", "initiate", "SKIP", None, "no application")


# ── 9. employees ──────────────────────────────────────────────────────────────────────────────
def employees() -> None:
    section("Employees")
    api.call("Employees", "list", "GET", "/enterprise/employees/")
    api.call("Employees", "departments", "GET", "/enterprise/employees/departments")
    _, e = api.call(
        "Employees",
        "create",
        "POST",
        "/enterprise/employees/",
        json={
            "employee_id": f"EMP-{uuid.uuid4().hex[:6].upper()}",
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": f"grace.{uuid.uuid4().hex[:6]}@example.com",
            "designation": "Engineer",
            "hire_date": "2026-09-01",
            "company_id": ctx.get("company_id"),
        },
    )
    if isinstance(e, dict) and e.get("id"):
        api.call("Employees", "read", "GET", f"/enterprise/employees/{e['id']}")
        api.call(
            "Employees",
            "update",
            "PATCH",
            f"/enterprise/employees/{e['id']}",
            json={"designation": "Senior Engineer"},
        )
        api.call("Employees", "delete", "DELETE", f"/enterprise/employees/{e['id']}")

    # Conversion needs an onboarding, so use the candidate the walk onboarded.
    if ctx.get("onboarded_candidate_id"):
        api.call(
            "Employees",
            "convert candidate to employee",
            "POST",
            f"/enterprise/employees/convert-candidate/{ctx['onboarded_candidate_id']}",
            json={"designation": "Engineer", "hire_date": "2026-10-01"},
        )


# ── 10. teardown of the job itself ────────────────────────────────────────────────────────────
def teardown() -> None:
    section("Cleanup")
    for key, path in (
        ("assessment_automation", "/enterprise/assessment/{}"),
        ("interview_automation", "/enterprise/interview-automation/{}"),
        ("mail_automation", "/enterprise/automation/mail/{}"),
        ("onboarding_automation", "/enterprise/onboarding-automation/{}"),
        ("template_id", "/enterprise/assessment-templates/{}"),
    ):
        if ctx.get(key):
            api.call("Cleanup", f"delete {key}", "DELETE", path.format(ctx[key]))
    if ctx.get("job_id"):
        api.call("Jobs", "delete job", "DELETE", f"/enterprise/jobs/{ctx['job_id']}")


def report() -> None:
    print("\n" + "=" * 74)
    print("REPORT")
    print("=" * 74)
    modules: dict[str, list[dict]] = {}
    for r in results:
        modules.setdefault(r["module"], []).append(r)
    for mod, rows in modules.items():
        p = sum(1 for r in rows if r["status"] == "PASS")
        f = sum(1 for r in rows if r["status"] == "FAIL")
        s = sum(1 for r in rows if r["status"] == "SKIP")
        flag = "OK  " if f == 0 else "FAIL"
        print(f"{flag} {mod:<14} {p} pass / {f} fail / {s} skip")
        for r in rows:
            if r["status"] != "PASS":
                print(f"       - [{r['status']}] {r['name']} ({r['code']}) {r['detail'][:160]}")
    total_f = sum(1 for r in results if r["status"] == "FAIL")
    print("-" * 74)
    print(f"{len(results)} checks, {total_f} failures")
    with open("e2e-report.json", "w", encoding="utf-8") as fh:
        json.dump({"account": ctx.get("email"), "results": results}, fh, indent=1, ensure_ascii=False)
    print("wrote e2e-report.json")


if __name__ == "__main__":
    t0 = time.time()
    if not signup():
        print("!! could not create an account — stopping")
        report()
        sys.exit(1)
    company_and_career_page()
    jobs()
    publishing()
    candidates()
    automations()
    assessment_flow()
    onboarding()
    employees()
    teardown()
    print(f"\n({time.time() - t0:.0f}s, account {ctx.get('email')})")
    report()
