# Croar Backend — Test Suite Documentation

> **1,204 automated tests · 100% passing · fully offline** (no network, no real database, no LLM calls)

This document is the complete reference for the Croar backend test suite
(`AppXcessTech/Croar_Backend`). It explains what is tested, how the suite is architected, how to
run it, how it isolates and parallelises tests, and how it stays correct as the codebase grows.

---

## Table of contents

1. [At a glance](#1-at-a-glance)
2. [Running the tests](#2-running-the-tests)
3. [Test architecture & isolation](#3-test-architecture--isolation)
4. [Test infrastructure (fixtures & mocking)](#4-test-infrastructure-fixtures--mocking)
5. [Introspection sweeps — self-maintaining coverage](#5-introspection-sweeps--self-maintaining-coverage)
6. [Per-router integration suites](#6-per-router-integration-suites)
7. [Croar Pilot (AI agent) tests](#7-croar-pilot-ai-agent-tests)
8. [Service-layer tests](#8-service-layer-tests)
9. [Unit tests](#9-unit-tests)
10. [What each category guarantees](#10-what-each-category-guarantees)
11. [Coverage philosophy & known limits](#11-coverage-philosophy--known-limits)
12. [Extending the suite](#12-extending-the-suite)
13. [Full file inventory](#13-full-file-inventory)

---

## 1. At a glance

| Layer | Tests | What it proves |
|-------|------:|----------------|
| **Unit** (`tests/unit/`) | 409 | Pure logic in isolation: agent helpers, JWT/bcrypt security, every Pydantic schema contract, deterministic service helpers |
| **Integration** (`tests/integration/`) | 727 | Real HTTP requests through the FastAPI app against a real (SQLite) database, plus DB-backed service & agent-tool logic |
| **Root suites** (`tests/`) | 68 | Admin / super-admin flows, security helpers, base smoke |
| **Total** | **1,204** | |

Every test runs **offline**: the database is SQLite in-memory, and all external dependencies
(LLM, MongoDB, candidate sourcing, SMTP email, Google Calendar) are mocked or never invoked.
There are **0 skips, 0 xfails, and 0 network calls**.

---

## 2. Running the tests

All commands run from the `backend/` directory using the project virtualenv.

```bash
# Full suite (runs in parallel across CPU cores by default)
python -m pytest

# Run serially (useful when debugging a single test — skips worker startup)
python -m pytest -n0

# A single layer
python -m pytest tests/unit
python -m pytest tests/integration

# A single file
python -m pytest tests/integration/test_jobs.py

# A single test
python -m pytest tests/integration/test_jobs.py::TestListJobs::test_happy_path_empty_list

# Helpful flags
python -m pytest -ra        # short summary of non-passing outcomes
python -m pytest -v         # verbose, one line per test
python -m pytest -x         # stop at first failure
```

`testpaths = ["tests"]` is configured, so a bare `pytest` only collects the suite under `tests/`.
Scratch scripts elsewhere in the repository are never collected.

### Pytest configuration (`pyproject.toml` → `[tool.pytest.ini_options]`)

```toml
asyncio_mode = "auto"                          # async tests need no @mark.asyncio
asyncio_default_fixture_loop_scope = "session" # one event loop per worker
asyncio_default_test_loop_scope = "session"
testpaths = ["tests"]
addopts = "-n auto"                            # parallelise across all CPU cores
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
filterwarnings = ["ignore::DeprecationWarning"]
```

### Required dev dependencies

- `pytest`, `pytest-asyncio` — test runner + async support
- `pytest-xdist` — parallel execution across workers
- `aiosqlite` — async SQLite driver for the in-memory test database
- `httpx` — async client used to drive the ASGI app

---

## 3. Test architecture & isolation

The suite is designed to be **fast, parallel, and perfectly isolated** without a real database.

### 3.1 Parallel execution

`addopts = "-n auto"` runs the suite across all available CPU cores via `pytest-xdist`. Each
worker is a **separate process with its own isolated in-memory database engine**, so workers
never share state. Tests are distributed across workers automatically.

> Determinism note: parametrized test ids must be identical across workers, so parametrized
> values use **fixed** ids (never `uuid4()` evaluated at collection time). A random id generated
> during collection would make each worker collect a different set of test ids and abort.

### 3.2 Build-schema-once, wipe-rows-between-tests

Recreating the full 61-table schema for every test is wasteful. Instead:

1. **`db_engine` is session-scoped** — the schema is built **once per worker** via
   `Base.metadata.create_all`.
2. An **autouse `_clean_tables` fixture** deletes every row from every table **after each test**
   (in reverse dependency order). This gives each test a pristine database without rebuilding the
   schema.

This pattern is both efficient and robust — it avoids per-test DDL and avoids fragile
cross-session SAVEPOINT juggling on SQLite. Combined with session-scoped event loops, the
session-scoped engine and the tests share a single loop per worker.

### 3.3 SQLite-as-Postgres shim — `tests/integration/conftest.py`

Production runs on PostgreSQL; the tests run on **SQLite in-memory** for zero-setup speed.
A startup shim (`_make_sqlite_compatible()`) rewrites Postgres-only column types and server
defaults so the full schema builds faithfully on SQLite:

| Postgres construct | Rewritten to (SQLite-safe) |
|--------------------|----------------------------|
| `JSONB`, `ARRAY` | generic `JSON` |
| `UUID` | SQLAlchemy `Uuid(as_uuid=True)` |
| `server_default = uuid_generate_v4()` | dropped (Python-side `default=uuid.uuid4` supplies it) |
| `'{}'::jsonb` and other `::` casts | the literal before the cast (e.g. `'{}'`), preserving `NOT NULL` |

The result is a schema structurally identical to production, so the integration tests exercise
the real models, relationships, and constraints.

---

## 4. Test infrastructure (fixtures & mocking)

### 4.1 Key fixtures (`tests/integration/conftest.py`)

| Fixture | Purpose |
|---------|---------|
| `db_engine` | Session-scoped SQLite in-memory engine (`StaticPool`); schema created once per worker |
| `_clean_tables` | Autouse — wipes all tables after each test for isolation |
| `db_session` | A fresh `AsyncSession` per test for seeding / asserting data directly |
| `override_db` | Overrides the app's `get_db` dependency to use the test database |
| `client` | `httpx.AsyncClient` over `ASGITransport` — a real request/response cycle, no TCP/network |
| `seed_company` | Inserts a `Company` (name + slug) and returns it (most endpoints are company-scoped) |
| `auth_user` | Factory → builds a stand-in authenticated user with a chosen company + permission set |
| `as_user` | Overrides `get_current_user` so a request runs as a given user |

**Granting permissions in a test:**

```python
# Authenticated user who may READ jobs:
as_user(auth_user(seed_company.id, perms=[(ModuleScope.jobs, PermissionAction.read)]))

# Authenticated but unprivileged (for 403 assertions):
as_user(auth_user(seed_company.id, perms=[]))

# No as_user call at all  -> unauthenticated (for 401 assertions)
```

### 4.2 Mocking strategy — staying fully offline

| Real dependency | How it is neutralised in tests |
|-----------------|--------------------------------|
| LLM (LangGraph / ChatOpenAI agent) | `monkeypatch` on `hr_agent_executor.ainvoke` and the question-generator services |
| MongoDB (Pilot / sourcing chat history) | `monkeypatch` on the Mongo collection handle; endpoints degrade gracefully |
| Candidate sourcing (Oxylabs / platform scrapers) | `monkeypatch` on `search_all_platforms` — no network |
| SMTP / email | send paths are patched or simply not invoked by the tested code path |
| Google Calendar / Meet | credential file absent → the helper returns `None` without a network call |

---

## 5. Introspection sweeps — self-maintaining coverage

Five sweeps enumerate the **live FastAPI route table** and generate one parametrized test per
endpoint. They require **zero per-endpoint maintenance** — add a new route and it is covered the
next time the suite runs.

| File | Tests | Discovers | Asserts |
|------|------:|-----------|---------|
| `test_auth_sweep.py` | 185 | Every route whose dependency tree contains `get_current_user` | Anonymous request → **401** |
| `test_rbac_sweep.py` | 170 | Every route carrying a `PermissionChecker` | Authenticated, zero-permission user → **403** |
| `test_notfound_sweep.py` | 33 | Every "by-id" `GET`/`DELETE` route (path ends in a path param) | Permitted user + unknown id → **404** |
| `test_validation_sweep.py` | 43 | Every `POST`/`PUT`/`PATCH` route with a required body field | Permitted user + empty body → **400/422** |
| `test_list_sweep.py` | 40 | Every no-parameter collection `GET` | Permitted user on empty DB → **200** |

### How discovery works

Each sweep walks `app.routes` and inspects every route's `.dependant` dependency tree:

- The **auth** and **RBAC** sweeps check for the presence of `get_current_user` /
  `PermissionChecker`.
- The **404 / validation / list** sweeps read the route's own required `(module, action)`
  straight off its `PermissionChecker`, then grant the test user *exactly* that permission — so
  the request reaches the handler (or body validation) instead of stopping at 401/403.

### Documented exclusions

Each sweep skips a small, **explicitly commented** set of endpoints where its blanket assertion
would not apply:

- Multipart / streaming routes (`/upload`, `/audio`, `/documents/`, `/ws`)
- Mongo-backed sourcing chat (`/sourcing/chat`) — different id semantics
- `super-admin/system` — non-SQLite id handling
- `simulations/scenarios` bulk delete — idempotent (returns 200 even when the row is absent)
- `super-admin/tenants/{id}` sub-resource creates — look up the absent tenant before body validation

These exclusions affect only *which sweep* skips an endpoint — most are still covered by the
auth/RBAC sweeps and/or their dedicated router suite.

---

## 6. Per-router integration suites

Each router has a dedicated suite following the same exhaustive template:
**401 (no auth) · 403 (wrong permission) · happy path · body validation · 404 · tenant isolation.**

| File | Tests | Area |
|------|------:|------|
| `test_jobs.py` | 15 | Job requisitions CRUD + public visibility |
| `test_employees.py` | 14 | Employee directory |
| `test_projects.py` | 14 | Projects & tasks |
| `test_company.py` | 14 | Company profile / settings |
| `test_communication.py` | 15 | Mail templates & automations |
| `test_assessment_templates.py` | 14 | Assessment templates |
| `test_interview_templates.py` | 14 | Interview templates |
| `test_onboarding_templates.py` | 14 | Onboarding templates |
| `test_automation.py` | 17 | Pipeline automations |
| `test_applications.py` | 10 | Candidate applications, stages, bulk delete |
| `test_candidates.py` | 3 | Candidate records |
| `test_team.py` | 13 | Team / roles |
| `test_survey_x360_simulation.py` | 17 | Surveys, 360° feedback, simulations |
| `test_dashboard_onboarding.py` | 9 | Dashboard stats + onboarding magic-link flow |
| `test_platform.py` | 7 | Platform super-admin surface (`platform:moderate`) |
| `test_sourcing.py` | 5 | Sourcing auth surface (live network search excluded) |
| `test_public.py` | 4 | Public careers / job endpoints (no auth) |
| `test_auth.py` | 7 | `/token` login flow (a real `EnterpriseUser` is seeded) |

---

## 7. Croar Pilot (AI agent) tests

The Pilot is the highest-risk product surface; it is tested end-to-end **without ever calling the
LLM**.

| File | Tests | What it covers |
|------|------:|----------------|
| `test_agents.py` | 18 | `/chat` endpoint — auth, company check, empty-message validation, and `pilot_action` extraction from tool messages (LLM / Mongo / sourcing / SMTP all mocked) |
| `test_agent_tools_db.py` | 17 | The Pilot's job tools run against the **real DB**: create / list / update / delete, invalid ids, tenant scoping, no-op updates, and the **"delete preserves hired candidates"** business rule |
| `test_e2e_pipeline.py` | 3 | The full `build_hiring_pipeline`: one tool call → a live job + four automations + three role-specific templates + generated interview time-slots |
| `tests/unit/test_agent_helpers.py` | 34 | Helper functions: `_clamp_int`, interview/assessment type normalization, date parsing, time-slot generation, the default onboarding form |

---

## 8. Service-layer tests

The service layer (`app/services/`) is exercised **indirectly** by every integration test that
hits a router, and **directly** by dedicated unit tests for its deterministic and DB-backed logic.

### 8.1 Pure-logic services — `tests/unit/test_services_pure.py` (24)

No database, no network — deterministic helpers tested in isolation:

| Service | Functions covered | Examples asserted |
|---------|-------------------|-------------------|
| `interview_service` | `parse_time()` | `"14:30"` → 14:30; garbage / out-of-range → safe 09:00 default |
| `onboarding_service` | `generate_onboarding_code()` | matches `ONB-#####`; cryptographically unique across many draws |
| `employee_service` | `_parse_date()` | date passthrough, ISO parsing, ISO+time suffix, bad/empty/None → default |
| `automation_service` | `evaluate_criteria()` | `ai_score > 80` gate passes/fails; missing score = 0; malformed gate **fails closed**; non-numeric criteria → true |
| `sourcing_service` | `register_provider()`, `search()` | unknown platform → `[]`; case-insensitive lookup; register overrides existing; default providers present |

### 8.2 DB-backed services — `tests/integration/test_services_db.py` (12)

Run against the SQLite test database:

| Service | Functions covered | Examples asserted |
|---------|-------------------|-------------------|
| `interview_service` | `find_available_slot()` | returns a future **weekday** at the configured start time; honours custom `time_slots`; respects `start_date` |
| `employee_service` | `EmployeeService.generate_employee_id()` | first id = `EMP-1001`; increments from existing; **numeric not lexicographic** (`EMP-1000` ranks above `EMP-999`); scoped per company |
| `enterprise_service` | `get_companies()`, `get_jobs()`, `create_job_requirement()`, `publish_job()` | empty results, created rows are returned, publishing creates a `PUBLISHED` posting |

### 8.3 What is intentionally not unit-tested at the service layer

LLM/network-bound services — `ai_evaluator`, `ai_service`, `ai_interview_service`, `hiring_agent`,
`imap_service`, `google_jobs`, the email-sending paths, and the ~35 individual sourcing scrapers —
are **exercised indirectly** through the integration tests but have no isolated unit tests, because
unit-testing them in isolation requires heavy mocking and yields brittle, low-value tests. Their
behaviour is validated where it matters (the `/chat`, pipeline, and sourcing flows).

---

## 9. Unit tests

| File | Tests | What it covers |
|------|------:|----------------|
| `tests/unit/test_schema_sweep.py` | 339 | **Auto-discovers every Pydantic schema** under `app.schemas` and pins its contract: schemas with required fields reject empty construction; all-optional schemas accept it; all fields are introspectable |
| `tests/unit/test_agent_helpers.py` | 34 | Agent helper logic (see §7) |
| `tests/unit/test_services_pure.py` | 24 | Deterministic service helpers (see §8.1) |
| `tests/unit/test_security_core.py` | 12 | `verify_password` / `get_password_hash` / `create_access_token` / `create_refresh_token` / `decode_token` |
| `tests/test_security_helpers.py` | 25 | Additional security / permission helper coverage |
| `tests/test_super_admin.py` | 28 | Super-admin flows |
| `tests/test_admin_enterprise.py` | 13 | Enterprise admin flows |
| `tests/test_base.py` | 2 | Base / smoke |

The **schema sweep** is the single largest source of coverage and is fully self-maintaining: every
new request/response model added under `app.schemas` is validated automatically.

---

## 10. What each category guarantees

- **Authentication** — *no* authenticated endpoint can be reached anonymously (185 checks).
- **Authorization** — *no* permission-gated endpoint can be reached without the right permission
  (170 checks).
- **Input validation** — write endpoints reject malformed / empty bodies (43 checks) with the
  correct client error.
- **Resource scoping** — requesting another tenant's resource or an unknown id returns 404 —
  never another tenant's data and never a 500 (33 checks + per-router isolation tests).
- **Happy paths** — collection endpoints return clean 200s on an empty DB (40 checks); CRUD flows
  create / read / update / delete correctly (per-router suites).
- **Schema integrity** — every Pydantic model's required/optional contract holds (339 checks).
- **Service logic** — deterministic helpers and DB-backed service functions behave correctly,
  including edge cases that fail safely (36 checks).
- **Product-critical agent flows** — the Pilot's pipeline build and job tools behave correctly,
  including the rule that deleting a job preserves hired candidates.

---

## 11. Coverage philosophy & known limits

- **Breadth via sweeps, depth via suites.** The five sweeps assert *status-code contracts* across
  the entire route table (breadth). The per-router suites, service tests, agent-tool tests, and
  e2e test assert *behaviour* — real DB writes, business rules, and response bodies (depth).
- **Offline by design.** External systems are mocked, so the suite is deterministic and can run in
  CI without secrets or services.
- **Not covered (by design):** real PostgreSQL-only behaviours (e.g. JSONB operators), real
  third-party API responses, multipart upload internals, and the individual sourcing scrapers.
  These belong to a separate, infrastructure-backed test tier if/when needed.

---

## 12. Extending the suite

- **New endpoint?** The auth / RBAC / 404 / validation / list sweeps cover it automatically. Add a
  dedicated `test_<router>.py` for its happy-path behaviour using the
  401/403/happy/validation/404/isolation template.
- **New schema?** The schema sweep covers its contract automatically. Add targeted tests only for
  custom validators or computed fields.
- **New agent tool?** Add a DB-backed test in `test_agent_tools_db.py` following the existing
  pattern (`_cfg`, `_make_job`, `.ainvoke(...)`).
- **New service function?** If it's pure logic, add to `tests/unit/test_services_pure.py`; if it
  touches the DB, add to `tests/integration/test_services_db.py`.
- **New external dependency?** Add a `monkeypatch` in the relevant test/conftest so the suite stays
  offline.
- **Parametrized tests under xdist:** never use `uuid4()` (or any non-deterministic value) in a
  `parametrize` list — use a fixed constant so every worker collects identical test ids.

---

## 13. Full file inventory

### `tests/unit/` — 409

| File | Tests |
|------|------:|
| `test_schema_sweep.py` | 339 |
| `test_agent_helpers.py` | 34 |
| `test_services_pure.py` | 24 |
| `test_security_core.py` | 12 |

### `tests/integration/` — 727

| File | Tests |
|------|------:|
| `test_auth_sweep.py` | 185 |
| `test_rbac_sweep.py` | 170 |
| `test_validation_sweep.py` | 43 |
| `test_list_sweep.py` | 40 |
| `test_notfound_sweep.py` | 33 |
| `test_agents.py` | 18 |
| `test_agent_tools_db.py` | 17 |
| `test_automation.py` | 17 |
| `test_survey_x360_simulation.py` | 17 |
| `test_jobs.py` | 15 |
| `test_communication.py` | 15 |
| `test_assessment_templates.py` | 14 |
| `test_interview_templates.py` | 14 |
| `test_onboarding_templates.py` | 14 |
| `test_employees.py` | 14 |
| `test_projects.py` | 14 |
| `test_company.py` | 14 |
| `test_team.py` | 13 |
| `test_services_db.py` | 12 |
| `test_applications.py` | 10 |
| `test_dashboard_onboarding.py` | 9 |
| `test_auth.py` | 7 |
| `test_platform.py` | 7 |
| `test_sourcing.py` | 5 |
| `test_public.py` | 4 |
| `test_candidates.py` | 3 |
| `test_e2e_pipeline.py` | 3 |

### `tests/` (root) — 68

| File | Tests |
|------|------:|
| `test_super_admin.py` | 28 |
| `test_security_helpers.py` | 25 |
| `test_admin_enterprise.py` | 13 |
| `test_base.py` | 2 |

**Grand total: 1,204 tests.**
