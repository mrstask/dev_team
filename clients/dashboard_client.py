"""Client for the langgraph_dashboard API — task management."""
from datetime import datetime, timezone

import httpx


class DashboardClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        # Cache: project_id → project dict (root_path, name, etc.)
        self._project_cache: dict[int, dict] = {}

    # ── Project helpers ──────────────────────────────────────────────────────

    def get_projects(self) -> list[dict]:
        with httpx.Client(timeout=30) as client:
            resp = client.get(f"{self.base_url}/projects")
            resp.raise_for_status()
        return resp.json()

    def get_project(self, project_id: int) -> dict:
        """Return project dict (cached). Raises ValueError if not found."""
        if project_id in self._project_cache:
            return self._project_cache[project_id]
        for p in self.get_projects():
            self._project_cache[p["id"]] = p
        if project_id not in self._project_cache:
            raise ValueError(f"Project {project_id} not found in dashboard")
        return self._project_cache[project_id]

    def get_project_root(self, project_id: int) -> str | None:
        """Return root_path for a project, or None if not set."""
        return self.get_project(project_id).get("root_path")

    # ── Task helpers ─────────────────────────────────────────────────────────

    def get_tasks(self, status: str | None = None, project_id: int | None = None) -> list[dict]:
        with httpx.Client(timeout=30) as client:
            resp = client.get(f"{self.base_url}/tasks")
            resp.raise_for_status()
        tasks = resp.json()
        if project_id is not None:
            tasks = [t for t in tasks if t["project_id"] == project_id]
        if status:
            tasks = [t for t in tasks if t["status"] == status]
        return tasks

    def get_task(self, task_id: int) -> dict:
        with httpx.Client(timeout=30) as client:
            resp = client.get(f"{self.base_url}/tasks")
            resp.raise_for_status()
        for t in resp.json():
            if t["id"] == task_id:
                return t
        raise ValueError(f"Task {task_id} not found")

    def move_task(self, task_id: int, status: str) -> dict:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{self.base_url}/tasks/{task_id}/move",
                json={"status": status},
            )
            resp.raise_for_status()
            return resp.json()

    def append_review_feedback(self, task_id: int, task: dict, review: dict) -> None:
        """Append reviewer issues to the task description so the next attempt sees them."""
        issues_text = "\n".join(f"- {i}" for i in review.get("issues", []))
        comment     = review.get("overall_comment", "")
        separator   = "\n\n---\nREVIEW FEEDBACK:\n"
        # Strip any previous feedback block before appending fresh one
        base_desc = task.get("description") or ""
        if "---\nREVIEW FEEDBACK:" in base_desc:
            base_desc = base_desc[:base_desc.index("---\nREVIEW FEEDBACK:")].rstrip()
        new_desc = f"{base_desc}{separator}{issues_text}\n\nOverall: {comment}"
        # Use update_task to preserve all fields
        self.update_task(task_id, {"description": new_desc})

    def create_task(
        self,
        title: str,
        description: str,
        status: str,
        priority: str,
        labels: list[str],
        project_id: int,
        parent_task_id: int | None = None,
        queue_position: int | None = None,
    ) -> int:
        """Create a new task in the dashboard. Returns the new task ID."""
        payload = {
            "title": title,
            "description": description,
            "status": status,
            "priority": priority,
            "labels": labels,
            "project_id": project_id,
            "assigned_agent_id": None,
        }
        if parent_task_id is not None:
            payload["parent_task_id"] = parent_task_id
        if queue_position is not None:
            payload["queue_position"] = queue_position
        with httpx.Client(timeout=30) as client:
            resp = client.post(f"{self.base_url}/tasks", json=payload)
            resp.raise_for_status()
            return resp.json()["id"]

    def update_task(self, task_id: int, updates: dict) -> dict:
        """Patch fields on a task. Fetches current state first to preserve all fields."""
        task = self.get_task(task_id)
        # Build full payload from current state — ai-ui TaskUpdate applies ALL fields,
        # so omitted fields default to None and wipe existing data.
        payload = {
            "title": task["title"],
            "description": task.get("description"),
            "short_description": task.get("short_description"),
            "implementation_description": task.get("implementation_description"),
            "definition_of_done": task.get("definition_of_done"),
            "status": task["status"],
            "priority": task["priority"],
            "assigned_agent_id": task.get("assigned_agent_id"),
            "human_owner": task.get("human_owner"),
            "labels": task.get("labels", []),
            "due_date": task.get("due_date"),
            "story_id": task.get("story_id"),
            "parent_task_id": task.get("parent_task_id"),
            "queue_position": task.get("queue_position"),
        }
        payload.update(updates)
        with httpx.Client(timeout=30) as client:
            resp = client.patch(f"{self.base_url}/tasks/{task_id}", json=payload)
            resp.raise_for_status()
            return resp.json()

    def set_labels(self, task_id: int, labels: list[str]) -> dict:
        """Replace all labels on a task."""
        return self.update_task(task_id, {"labels": labels})

    def get_subtasks(self, parent_task_id: int) -> list[dict]:
        """Return all tasks whose parent_task_id matches."""
        return [
            t for t in self.get_tasks()
            if t.get("parent_task_id") == parent_task_id
        ]

    def create_run(
        self,
        task_id: int,
        agent_id: int | None,
        pipeline_type: str = "dev_team",
    ) -> int:
        """Create a run record at the start of agent processing. Returns run ID or -1 on failure."""
        payload = {
            "task_id": task_id,
            "agent_id": agent_id,
            "pipeline_type": pipeline_type,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(f"{self.base_url}/runs", json=payload)
                if resp.status_code == 201:
                    return resp.json()["id"]
        except Exception:
            pass
        return -1

    def update_run(
        self,
        run_id: int,
        status: str,
        output_summary: str | None = None,
        output_payload: dict | None = None,
        error_message: str | None = None,
        logs_text: str | None = None,
    ) -> None:
        """Update a run record on completion or failure. Best-effort — silently ignores errors."""
        if run_id < 0:
            return
        payload: dict = {
            "status": status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        if output_summary is not None:
            payload["output_summary"] = output_summary
        if output_payload is not None:
            payload["output_payload"] = output_payload
        if error_message is not None:
            payload["error_message"] = error_message
        if logs_text is not None:
            payload["logs_text"] = logs_text
        try:
            with httpx.Client(timeout=30) as client:
                client.patch(f"{self.base_url}/runs/{run_id}", json=payload)
        except Exception:
            pass

    def get_agent_ids(self) -> dict[str, int]:
        """Return a mapping of agent slug → id for all registered agents."""
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(f"{self.base_url}/agents")
                if resp.status_code == 200:
                    return {a["slug"]: a["id"] for a in resp.json() if a.get("slug")}
        except Exception:
            pass
        return {}

    def log_event(self, task_id: int, event_type: str, payload: dict) -> None:
        """Log an inter-agent communication event. Best-effort — silently ignores errors."""
        try:
            with httpx.Client(timeout=10) as client:
                client.post(
                    f"{self.base_url}/activity-events",
                    json={"entity_type": "task", "entity_id": task_id, "event_type": event_type, "payload": payload},
                )
        except Exception:
            pass

    def get_task_events(self, task_id: int) -> list[dict]:
        """Return all activity events for a task, ordered by creation time."""
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(f"{self.base_url}/activity-events", params={"task_id": task_id})
                if resp.status_code == 200:
                    return resp.json()
        except Exception:
            pass
        return []

    def create_suggestion(
        self,
        task_id: int,
        agent_role: str,
        issue_pattern: str,
        suggested_instruction: str,
        evidence: list,
    ) -> dict | None:
        """Store a prompt improvement suggestion. Returns created record or None on failure."""
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    f"{self.base_url}/prompt-suggestions",
                    json={
                        "task_id": task_id,
                        "agent_role": agent_role,
                        "issue_pattern": issue_pattern,
                        "suggested_instruction": suggested_instruction,
                        "evidence": evidence,
                    },
                )
                if resp.status_code == 201:
                    return resp.json()
        except Exception:
            pass
        return None

    def get_suggestions(self, status: str | None = None) -> list[dict]:
        """Return prompt suggestions, optionally filtered by status."""
        try:
            params = {"status": status} if status else {}
            with httpx.Client(timeout=10) as client:
                resp = client.get(f"{self.base_url}/prompt-suggestions", params=params)
                if resp.status_code == 200:
                    return resp.json()
        except Exception:
            pass
        return []

    def sync_agents(self, roles_dict: dict) -> None:
        """Register missing roles as agents in the dashboard."""
        with httpx.Client(timeout=30) as client:
            # 1. Fetch existing agents
            resp = client.get(f"{self.base_url}/agents")
            if resp.status_code == 200:
                existing_slugs = {a.get("slug") for a in resp.json() if a.get("slug")}
            else:
                existing_slugs: set[str] = set()

            # 2. Register missing agents
            for slug, info in roles_dict.items():
                if slug not in existing_slugs:
                    payload = {
                        "name": info.get("name", slug),
                        "slug": slug,
                        "description": info.get("description", ""),
                        "status": "online",
                        "agent_type": "custom",
                        "capabilities": []
                    }
                    client.post(f"{self.base_url}/agents", json=payload)
