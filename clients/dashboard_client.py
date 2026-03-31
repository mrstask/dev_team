"""Client for the langgraph_dashboard API — task management."""
from datetime import datetime, timezone

import httpx


class DashboardClient:
    def __init__(self, base_url: str, project_id: int):
        self.base_url = base_url.rstrip("/")
        self.project_id = project_id

    def get_tasks(self, status: str | None = None) -> list[dict]:
        with httpx.Client(timeout=30) as client:
            resp = client.get(f"{self.base_url}/tasks")
            resp.raise_for_status()
        tasks = [t for t in resp.json() if t["project_id"] == self.project_id]
        if status:
            tasks = [t for t in tasks if t["status"] == status]
        return tasks

    def get_task(self, task_id: int) -> dict:
        for t in self.get_tasks():
            if t["id"] == task_id:
                return t
        raise ValueError(f"Task {task_id} not found in project {self.project_id}")

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
        base_desc = task.get("description", "")
        if "---\nREVIEW FEEDBACK:" in base_desc:
            base_desc = base_desc[:base_desc.index("---\nREVIEW FEEDBACK:")].rstrip()
        new_desc = f"{base_desc}{separator}{issues_text}\n\nOverall: {comment}"
        payload = {
            "title":             task["title"],
            "description":       new_desc,
            "status":            task["status"],
            "priority":          task["priority"],
            "assigned_agent_id": task.get("assigned_agent_id"),
            "labels":            task.get("labels", []),
        }
        with httpx.Client(timeout=30) as client:
            client.patch(f"{self.base_url}/tasks/{task_id}", json=payload)

    def create_task(
        self,
        title: str,
        description: str,
        status: str,
        priority: str,
        labels: list[str],
        parent_task_id: int | None = None,
    ) -> int:
        """Create a new task in the dashboard. Returns the new task ID."""
        payload = {
            "title": title,
            "description": description,
            "status": status,
            "priority": priority,
            "labels": labels,
            "project_id": self.project_id,
            "assigned_agent_id": None,
        }
        if parent_task_id is not None:
            payload["parent_task_id"] = parent_task_id
        with httpx.Client(timeout=30) as client:
            resp = client.post(f"{self.base_url}/tasks", json=payload)
            resp.raise_for_status()
            return resp.json()["id"]

    def update_task(self, task_id: int, updates: dict) -> dict:
        """Patch arbitrary fields on a task."""
        with httpx.Client(timeout=30) as client:
            resp = client.patch(f"{self.base_url}/tasks/{task_id}", json=updates)
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
                        "agent_type": "dev_team",
                        "capabilities": []
                    }
                    client.post(f"{self.base_url}/agents", json=payload)
