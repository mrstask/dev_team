"""PMAnalysisAgent — post-mortem analysis of a completed task to improve agent prompts."""
import json

from rich.panel import Panel

import config
from clients import DashboardClient
from core import create_client, parse_json_response, stream_chat_with_display
from prompts.pm_analysis import PM_ANALYSIS_SYSTEM_PROMPT, PM_ANALYSIS_USER_PROMPT_HEADER

_db = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)


class PMAnalysisAgent:
    """Analyses the full event log of a completed task and stores prompt improvement suggestions."""

    def __init__(self):
        self.client = create_client("pm_analysis")

    def run(self, task_id: int) -> None:
        """Fetch events for task_id, analyse, and persist suggestions. Best-effort."""
        console = config.console
        config.print_agent_rule("PM — Post-mortem Analysis", "pm_analysis")

        try:
            task = _db.get_task(task_id)
            events = _db.get_task_events(task_id)

            if not events:
                console.print("[dim]  No events to analyse — skipping.[/dim]")
                return

            user_prompt = self._build_prompt(task, events)
            final_resp, _ = stream_chat_with_display(
                self.client,
                messages=[
                    {"role": "system", "content": PM_ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                timeout=300,
            )
            content = final_resp.get("message", {}).get("content", "")
            raw = parse_json_response(content)
            suggestions = raw.get("suggestions", [])

            if not suggestions:
                console.print("[dim]  No prompt improvements identified.[/dim]")
                return

            saved = 0
            for s in suggestions:
                result = _db.create_suggestion(
                    task_id=task_id,
                    agent_role=s.get("agent_role", "unknown"),
                    issue_pattern=s.get("issue_pattern", ""),
                    suggested_instruction=s.get("suggested_instruction", ""),
                    evidence=s.get("evidence", []),
                )
                if result:
                    saved += 1

            console.print(Panel(
                f"[bold cyan]{saved} suggestion(s) stored[/bold cyan]\n"
                + "\n".join(
                    f"  [{s.get('agent_role')}] {s.get('issue_pattern', '')[:80]}"
                    for s in suggestions
                ),
                border_style="cyan",
                title="PM — Post-mortem",
            ))

        except Exception as e:
            console.print(f"[yellow]  PM analysis error (non-fatal): {e}[/yellow]")

    @staticmethod
    def _build_prompt(task: dict, events: list[dict]) -> str:
        lines = [PM_ANALYSIS_USER_PROMPT_HEADER.format(
            task_id=task["id"],
            title=task["title"],
            event_count=len(events),
        )]
        for ev in events:
            payload_str = json.dumps(ev.get("payload", {}), ensure_ascii=False)
            if len(payload_str) > 600:
                payload_str = payload_str[:600] + " ..."
            lines.append(f"[{ev.get('created_at', '?')}] {ev['event_type']}: {payload_str}")
        return "\n".join(lines)
