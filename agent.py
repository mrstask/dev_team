"""DevAgent — ReAct loop over Ollama/OpenRouter with tool calling."""
from rich.rule import Rule

import config
from llm import create_client
from react_loop import run_react_loop
from roles import ROLES


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
    ) -> dict | None:
        """ReAct loop: read context -> write files."""
        messages = [
            {"role": "system", "content": self.role_def["system_prompt"]},
            {"role": "user", "content": self._build_prompt(task, feedback, skeleton_files, previous_files)},
        ]

        style = "cyan" if self._backend == "openrouter" else "yellow"
        config.console.print(Rule(
            f"[bold]{self.role_def['name']}[/bold]  ·  {self._backend}  ·  {self.model}",
            style=style,
        ))

        return run_react_loop(self.client, messages)

    def _build_prompt(
        self,
        task: dict,
        feedback: str = "",
        skeleton_files: list[dict] | None = None,
        previous_files: list[dict] | None = None,
    ) -> str:
        labels = ", ".join(task.get("labels", []))
        prompt = (
            f"Task: {task['title']}\n"
            f"Priority: {task['priority']}\n"
            f"Labels: {labels}\n\n"
            f"Description:\n{task.get('description', 'No description.')}\n\n"
            "Instructions:\n"
            "1. Use read_file / list_files / search_code to gather context if needed.\n"
            "2. Implement the task completely and correctly.\n"
            "3. Call write_files with ALL created/modified files and a summary.\n"
        )
        if skeleton_files:
            prompt += f"\nSkeleton files from Architect ({len(skeleton_files)} files):\n"
            for f in skeleton_files:
                prompt += f"\n=== {f['path']} ===\n{f['content']}\n"
            prompt += "\nImplement every TODO in the skeleton files above. Return complete files.\n"
        if previous_files:
            prompt += (
                f"\nYour previous attempt produced {len(previous_files)} file(s). "
                "They are included below — do NOT re-read them from disk, use these versions as your starting point. "
                "Fix only what the reviewer flagged; keep everything else intact:\n"
            )
            for f in previous_files:
                prompt += f"\n=== {f['path']} ===\n{f['content']}\n"
        if feedback:
            prompt += f"\nReviewer feedback to address:\n{feedback}\n"
        return prompt
