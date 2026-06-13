from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from app.agents.state import AgentState
from app.agents.tools import (
    create_job_requisition,
    generate_draft_offer,
    generate_job_description,
    initiate_candidate_onboarding,
    score_candidate_application,
)
from app.core.settings import get_settings

_settings = get_settings()

# 1. Setup LLM and Tools
tools = [
    score_candidate_application,
    initiate_candidate_onboarding,
    generate_draft_offer,
    generate_job_description,
    create_job_requisition,
]
llm = ChatOpenAI(api_key=_settings.openai_api_key, model=_settings.openai_model)
llm_with_tools = llm.bind_tools(tools)

from langchain_core.messages import SystemMessage

SYSTEM_PROMPT = """
You are the Croar AI HR Agent, a proactive Operating System for HR tasks.
Your goal is to be DECISIVE, AUTONOMOUS, and EFFICIENT.

CRITICAL DIRECTIVES:
1. DONT ASK, TELL: Don't ask for permission for logical next steps. If a user asks for a JD, draft it AND create the live requisition in the database immediately.
2. PROACTIVE CHAINING: If you see a candidate has a high score, suggest or initiate the next stage (like assessment or onboarding) automatically.
3. NEURAL WORKFLOWS: Always include professional interview rounds when creating jobs.
4. COMMUNICATE ACTION: Tell the user exactly what you HAVE done (e.g., "I have created the job and set it to LIVE").
5. PREMIUM TONE: You are a high-end AI executive. Be concise, professional, and helpful.
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
def create_hr_graph():
    workflow = StateGraph(AgentState)
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
        if last_message.tool_calls:
            return "action"
        return END

    workflow.add_conditional_edges("agent", should_continue, {"action": "action", END: END})

    # Edge from action back to agent to process the result
    workflow.add_edge("action", "agent")

    return workflow.compile(checkpointer=checkpointer)


# Singleton instance of the graph
hr_agent_executor = create_hr_graph()
