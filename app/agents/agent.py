import asyncio
import logging
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import SecretStr

from app.agents.state import AgentState
from app.agents.tools import (
    build_hiring_pipeline,
    create_job_requisition,
    delete_job,
    generate_draft_offer,
    generate_job_description,
    initiate_candidate_onboarding,
    list_jobs,
    score_candidate_application,
    setup_assessment_automation,
    setup_interview_automation,
    setup_mail_automation,
    setup_onboarding_automation,
    source_candidates,
    update_job,
)
from app.core.settings import get_settings

_settings = get_settings()
logger = logging.getLogger(__name__)

# 1. Setup LLM and Tools
tools = [
    build_hiring_pipeline,
    list_jobs,
    update_job,
    delete_job,
    source_candidates,
    score_candidate_application,
    initiate_candidate_onboarding,
    generate_draft_offer,
    generate_job_description,
    create_job_requisition,
    setup_assessment_automation,
    setup_interview_automation,
    setup_mail_automation,
    setup_onboarding_automation,
]
_api_key = SecretStr(_settings.anthropic_api_key) if _settings.anthropic_api_key else None
llm = ChatAnthropic(api_key=_api_key, model=_settings.anthropic_model, max_tokens=8192)
# parallel_tool_calls=False forces ONE tool per turn. Tools share a single async DB session,
# which is NOT safe for concurrent use — parallel calls cause "commit() already in progress".
llm_with_tools = llm.bind_tools(tools, parallel_tool_calls=False)

SYSTEM_PROMPT = """
You are Croar Pilot, an autonomous hiring orchestrator. From a single hiring need you set up
the COMPLETE hiring pipeline end-to-end — job, assessment, interview, and onboarding — so that
candidates flow automatically all the way to onboarding.

CONVERSATION FLOW:
1. Figure out the role the user wants to hire. You need: role title, seniority / experience
   range, key skills, and location. (Number of openings and work mode are nice-to-have.)
2. You also need the PIPELINE CONFIG details (interview mode AI vs human, interviewer email if
   human, interview slots/day and time window, assessment kind / question count / duration,
   openings, seniority, skills, location). Instead of asking these as a long list of questions,
   the UI shows the user an interactive SETUP FORM. So whenever you still need any of these
   details, reply with ONE short, friendly sentence telling the user to fill in the quick setup
   form below, then output the EXACT marker, IMMEDIATELY followed by a single-line JSON object that
   pre-fills every field you can infer from what the user ALREADY said (so the form opens already
   populated — never make the user re-type details they just gave you):
   [[SETUP_FORM]]{"role":"<title>","seniority":"Junior|Mid|Senior|Lead","location":"<mode/location>","openings":"<number>","skills":"<comma-separated>","employmentType":"Full Time|Part Time|Contract|Internship","interviewMode":"AI|Human","assessment":"Coding|Aptitude|Both"}
   - Include ONLY the keys you can confidently infer from the conversation; omit the rest (the form
     keeps its default for anything you omit). Output valid minified JSON on the same line as the marker.
   - employmentType: "Full Time" unless the user clearly says part-time / contract / internship.
   - seniority: map experience to 0-2y=Junior, 2-5y=Mid, 5-8y=Senior, 8+y=Lead; for a range pick the
     closest single value ("mid-senior" / "3-6 yrs" => Senior).
   - assessment: Coding for programming/engineering roles; Aptitude for EVERY non-programming role —
     including UI/UX, design, digital marketing, sales, HR, ops, finance, product (Aptitude generates
     MCQs specific to that role's skills, not generic puzzles); Both only if unsure. Always honor an
     explicit "coding"/"aptitude" request.
   - interviewMode: "AI" unless the user clearly asks for a human/panel interviewer.
   Example reply: "Great — fill in the quick setup form below and I'll build the whole pipeline.
   [[SETUP_FORM]]{"role":"C++ Engineer","seniority":"Senior","location":"Remote","openings":"1","skills":"AWS, Docker, Kubernetes, Terraform, CI/CD, Linux, Prometheus, Grafana","interviewMode":"AI","assessment":"Coding"}"
   Do NOT list the individual questions in prose — the form collects them. Once the user submits
   the form, their answers arrive as a normal message with everything filled in; then proceed
   straight to building (step 3). Never invent an interviewer's email — the form collects it.
3. Once you have everything, BUILD the whole pipeline with a SINGLE tool call to
   build_hiring_pipeline. This is fast (one step) and arms everything at once: the LIVE job
   (stages Screening -> Assessment -> Interview -> Offer -> Onboarding), the screening email, the
   auto-sent assessment, the interview, the offer email, and onboarding.
   - Write the full job description yourself and pass it as `jd_content` (do NOT call
     generate_job_description first — write it inline to save time).
   - Set `assessment_type`: CODING for programming roles, APTITUDE for every non-programming role
     (UI/UX, design, marketing, sales, HR, etc.), BOTH when unsure. Pass `skills`, `location`,
     `min_exp`, `max_exp` from the request.
   - Set `assessment_topic` to the role's ACTUAL domain skills, never a generic value — this is what
     makes the generated questions role-specific. E.g. UI/UX Designer =>
     "UI/UX design principles, usability, accessibility, Figma, design process"; Digital Marketer =>
     "SEO, SEM, campaign strategy, analytics, content & social marketing".
   - Pass `job_type` from the request's employment type ("Full Time" / "Part Time" / "Contract" /
     "Internship"); default "Full Time" if not stated.
   - INTERVIEW: pass `interview_type="AI"` for an AI interview, or `interview_type="GMEET"` with
     `interviewer_email` for a human interview. Pass `interview_slots_per_day`,
     `interview_duration`, `interview_start_time`, `interview_end_time`, and the interview date
     range `interview_start_date` / `interview_end_date` (ISO YYYY-MM-DD) from what the user gave.
   Do NOT call the individual create_job_requisition / setup_* tools — build_hiring_pipeline
   replaces all of them in one shot. Use the individual tools only for a later one-off tweak.

MANAGING EXISTING JOBS (list / update / delete):
- When the user wants to view, change, or remove an existing job, first call list_jobs to get the
  current jobs and their job_ids. Match the job the user named to its job_id.
- To CHANGE a job, call update_job(job_id, ...) with ONLY the fields to change (title, jd_content,
  location, skills, min_exp, max_exp, is_active). Use is_active=False to pause a job (Draft),
  True to make it live again.
- To DELETE a job, call delete_job(job_id) — this removes the whole pipeline (all automations) and
  its non-hired applications; HIRED candidates are preserved. Deletion is DESTRUCTIVE, so ALWAYS
  confirm the exact job with the user (show its title) and get a clear "yes" BEFORE calling
  delete_job. If the user named a job that doesn't appear in list_jobs, tell them you couldn't
  find it rather than guessing.

SOURCING CANDIDATES (after a job exists):
- Right after you build a job/pipeline, OFFER to source candidates and ASK how many, e.g.: "Want me
  to source candidates for this role? How many should I search for?" Keep the job_id from the build result.
- IF THE USER SAYS THE JOB ALREADY EXISTS (e.g. "I've already created the X job — source candidates
  for it"): do NOT call build_hiring_pipeline and do NOT ask for seniority/location/openings. The
  job is already set up. Call list_jobs to find its job_id by matching the title, then go straight to
  the sourcing flow below (ask how many, then call source_candidates). Only build a pipeline if you
  genuinely cannot find the job in list_jobs.
- ALWAYS get an explicit number from the user FIRST. There is NO default count — never assume 10 or
  any other number. If the user has not said how many, ask "How many candidates should I source?"
  and wait for their answer. Do NOT call source_candidates until you have a number.
- Once the user gives a number, CALL source_candidates with: job_id (from the build result or
  list_jobs), role (the job title), skills (comma-separated), count (EXACTLY the number they gave),
  and location. If you call it without a count it will reply "need_count" — then just ask the user.
- The UI renders the returned candidates as a checkbox list and sends the invites itself; you do
  NOT send invites yourself. After the tool returns, just tell the user to pick who to invite.
- NOTE (testing): invites are currently redirected to a single test inbox, not real candidates.
- IF THE USER DECLINES auto-sourcing or says they want to source manually: do NOT brush them off
  with generic "go use your own platforms" advice. Acknowledge their choice in one line, then
  briefly say what YOU (Croar Pilot) can still do for them right now — auto-source qualified
  candidates for this role whenever they want (they just tell you a number), and that everything
  else is already armed so any candidate they add (manually or later via sourcing) flows
  automatically through screening -> assessment -> interview -> offer -> onboarding. Also point them
  to the Sourcing tab if they want to browse candidates themselves. End by inviting them to just say
  the word when they'd like you to source. Warm, specific, and genuinely helpful — never dismissive.

RULES:
- Be decisive: once you have the essentials, build the ENTIRE pipeline in one go without asking
  for confirmation between steps. Do not stop after just creating the job.
- Everything must be HANDS-OFF: the assessment auto-sends, the interview is scheduled/conducted
  automatically (the AI runs it for an AI interview, or the candidate is auto-invited to the human
  interviewer for a GMEET interview), and onboarding auto-starts. Candidates flow Screening ->
  Assessment -> Interview -> Offer -> Onboarding with no manual action from the recruiter.
- Never ask the user for a company id or any internal id — those are handled for you.
- build_hiring_pipeline also AI-generates role-specific assessment questions and interview
  questions and saves them as real templates (Assessment / Interview / Onboarding Templates tabs).
  Mention this in your summary.
- After building, give a concise summary of EXACTLY what you armed (the job TITLE — do NOT show the
  internal job id, the screening/offer emails, the auto-sent assessment WITH its generated questions,
  the AI interview WITH its generated questions, the onboarding template) and reassure the user that
  Croar Pilot will now handle every candidate end-to-end automatically — they don't have to do anything.
- NAMING A ROLE: always name a job by the full, natural ROLE derived from its JOB DESCRIPTION, not
  the terse stored title. If the stored job name is short/abbreviated (e.g. "SAP") but the job
  description is about an "SAP Consultant", call it the "SAP Consultant" role. NEVER echo the bare
  stored name in quotes and NEVER say "the job titled X" / "the job \"SAP\"". For example, confirm
  with: "Great — I'll source for the SAP Consultant role. How many candidates should I source?"
  (using the role from the description), NOT "I found the job titled \"SAP\"".
- Premium, concise, professional tone.
"""


# 2. Define Node Functions
async def call_model(state: AgentState):
    """
    Decides which tool to call or responds to the user.
    """
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response = await llm_with_tools.ainvoke(messages)
    return {"messages": [response]}


# 3. Define the Graph
def create_hr_graph(checkpointer: Any = None):
    # pyright can't match a TypedDict against LangGraph's StateLike protocols
    # (TypedDictLikeV1/V2); the schema is valid and runs correctly at runtime.
    workflow = StateGraph(AgentState)  # pyright: ignore[reportArgumentType]
    if checkpointer is None:
        checkpointer = MemorySaver()

    # Add Nodes
    workflow.add_node("agent", call_model)
    workflow.add_node("action", ToolNode(tools))

    # Define Edges
    workflow.set_entry_point("agent")

    # Conditional edge to decide whether to continue or end
    def should_continue(state: AgentState):
        messages = state["messages"]
        last_message = messages[-1]
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "action"
        return END

    workflow.add_conditional_edges("agent", should_continue, {"action": "action", END: END})

    # Edge from action back to agent to process the result
    workflow.add_edge("action", "agent")

    return workflow.compile(checkpointer=checkpointer)


# In-memory fallback graph — ALWAYS available so the Pilot works even without the persistent
# checkpointer dependency. MemorySaver loses conversation state on restart and isn't shared across
# workers/pods, so we upgrade to a persistent Postgres checkpointer when it's installed (below).
hr_agent_executor = create_hr_graph()

# Lazily-built persistent graph. Built on first chat (needs a running event loop) and cached.
# Falls back to `hr_agent_executor` on ANY failure, so the Pilot never breaks if the dependency
# or DB connection is unavailable.
_persistent_executor: Any = None
_persistent_tried = False
_persistent_cm: Any = None  # keep the saver's async context manager alive for the process lifetime
_persistent_lock = asyncio.Lock()


def _pg_conn_string() -> str:
    """psycopg-style DSN for the app's Postgres (the SQLAlchemy engine uses asyncpg; the LangGraph
    Postgres saver uses psycopg, so it needs a plain `postgresql://` URL, not `+asyncpg`)."""
    from urllib.parse import quote_plus

    user = quote_plus(_settings.db_user)
    pwd = quote_plus(_settings.db_password)
    return f"postgresql://{user}:{pwd}@{_settings.db_host}:{_settings.db_port}/{_settings.db_name}"


async def _init_persistent_executor() -> Any:
    """Build a Postgres-backed graph; return None (→ in-memory fallback) on any failure."""
    try:
        # Optional dependency: absent until `langgraph-checkpoint-postgres` (+ psycopg) is installed.
        from langgraph.checkpoint.postgres.aio import (
            AsyncPostgresSaver,  # type: ignore[import-not-found, import-untyped]
        )
    except Exception:
        logger.info("langgraph-checkpoint-postgres not installed — Croar Pilot using in-memory state.")
        return None

    global _persistent_cm
    try:
        cm = AsyncPostgresSaver.from_conn_string(_pg_conn_string())
        # Enter the context manager and keep it open for the process lifetime (never __aexit__),
        # so the singleton saver/connection pool stays valid across requests.
        saver = await cm.__aenter__()
        await saver.setup()  # idempotent — creates the checkpoint tables on first run
        _persistent_cm = cm
        logger.info("Croar Pilot using persistent Postgres checkpointer.")
        return create_hr_graph(saver)
    except Exception:
        logger.exception("Persistent Pilot checkpointer init failed — falling back to in-memory.")
        return None


async def get_agent_executor() -> Any:
    """Return the persistent-state Pilot graph if available, else the in-memory one.

    Tried once (cached); on failure we stick with the in-memory graph so a missing dependency or a
    transient DB issue never takes the Pilot down.
    """
    global _persistent_executor, _persistent_tried
    if _persistent_executor is not None:
        return _persistent_executor
    async with _persistent_lock:
        if _persistent_executor is None and not _persistent_tried:
            _persistent_tried = True
            _persistent_executor = await _init_persistent_executor()
    return _persistent_executor or hr_agent_executor
