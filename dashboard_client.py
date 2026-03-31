"""Client for the langgraph_dashboard API — task management."""
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
