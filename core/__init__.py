"""Core infrastructure — LLM factory, ReAct loop, roles, tools."""
from .llm import LLMRateLimitError, LLMStallError, create_client, create_fallback_client, parse_json_response, stream_chat_with_display
from .react_loop import extract_text_tool_calls, run_react_loop
from .roles import ROLES, get_role_for_task
from .tools import TOOL_SPECS, RESEARCH_TOOL_SPECS, dispatch, project_context, get_project_root, set_project_root, clear_project_root

__all__ = [
    "LLMRateLimitError",
    "LLMStallError",
    "create_client",
    "create_fallback_client",
    "parse_json_response",
    "stream_chat_with_display",
    "extract_text_tool_calls",
    "run_react_loop",
    "ROLES",
    "get_role_for_task",
    "TOOL_SPECS",
    "RESEARCH_TOOL_SPECS",
    "dispatch",
    "project_context",
    "get_project_root",
    "set_project_root",
    "clear_project_root",
]
