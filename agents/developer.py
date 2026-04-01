"""DevAgent — ReAct loop over Ollama/OpenRouter with tool calling."""
import config
from core import ROLES, create_client, run_react_loop
from dtypes import DeveloperResult, FileContent
from prompts.developer import (
    DEVELOPER_FEEDBACK_PROMPT,
    DEVELOPER_PREVIOUS_FILES_HEADER,
    DEVELOPER_SKELETON_FOOTER,
    DEVELOPER_SKELETON_HEADER,
    DEVELOPER_TASK_PROMPT,
)


class DevAgent:
    def __init__(self, role: str, model: str | None = None):
        if role not in ROLES:
            raise ValueError(f"Unknown role: {role}. Available: {list(ROLES.keys())}")
        self.role = role
        self.role_def = ROLES[role]

        dev = config.step("developer")
        self._backend = dev["backend"]
        self.model = model or dev["model"]
        self.client = create_client("developer")

    def run(
        self,
        task: dict,
        feedback: str = "",
        skeleton_files: list[dict] | None = None,
        previous_files: list[dict] | None = None,
    ) -> DeveloperResult | None:
        """ReAct loop: read context -> write files."""
        messages = [
            {"role": "system", "content": self.role_def["system_prompt"]},
            {"role": "user", "content": self._build_prompt(task, feedback, skeleton_files, previous_files)},
        ]
        config.print_agent_rule(self.role_def["name"], "developer")

        raw = run_react_loop(self.client, messages)
        if not raw or not raw.get("files"):
            return None

        return DeveloperResult(
            files=[FileContent(**f) for f in raw["files"]],
            summary=raw.get("summary", ""),
        )

    @staticmethod
    def _build_prompt(
            task: dict,
        feedback: str = "",
        skeleton_files: list[dict] | None = None,
        previous_files: list[dict] | None = None,
    ) -> str:
        labels = ", ".join(task.get("labels", []))
        prompt = DEVELOPER_TASK_PROMPT.format(
            title=task["title"],
            priority=task["priority"],
            labels=labels,
            description=task.get("description", "No description."),
        )
        if skeleton_files:
            prompt += DEVELOPER_SKELETON_HEADER.format(count=len(skeleton_files))
            for f in skeleton_files:
                prompt += f"\n=== {f['path']} ===\n{f['content']}\n"
            prompt += DEVELOPER_SKELETON_FOOTER
        if previous_files:
            prompt += DEVELOPER_PREVIOUS_FILES_HEADER.format(count=len(previous_files))
            for f in previous_files:
                prompt += f"\n=== {f['path']} ===\n{f['content']}\n"
        if feedback:
            prompt += DEVELOPER_FEEDBACK_PROMPT.format(feedback=feedback)
        return prompt
