from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """
    Represents the state of the HR Agent Operating System.
    """

    # The history of the conversation between the user and the agent
    messages: Annotated[list[BaseMessage], add_messages]

    # The current focus of the agent (e.g. "recruitment", "onboarding")
    context: str

    # Data related to the current operation (candidate_id, application_id, etc.)
    metadata: dict

    # Any pending approvals required from the user
    pending_approvals: list[dict]

    # Final response to the user
    next_action: str
