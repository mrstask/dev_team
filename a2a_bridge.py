"""Local A2A bridge used to feed the external A2A Inspector."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from dtypes import A2AAttachment, A2AMessage


def _now() -> datetime:
    return datetime.now(timezone.utc)


class A2ALogStore:
    """Append-only JSONL store for internal agent handoff messages."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def publish(
        self,
        *,
        task: dict | None,
        from_agent: str,
        to_agent: str,
        kind: str,
        summary: str = "",
        payload: dict[str, Any] | None = None,
        attachments: list[A2AAttachment] | None = None,
        task_id: int | None = None,
        task_title: str = "",
        task_status: str = "",
        priority: str = "",
        parent_task_id: int | None = None,
    ) -> A2AMessage:
        payload = payload or {}
        attachments = attachments or []
        if task is not None:
            task_id = task.get("id", task_id)
            task_title = task.get("title", task_title)
            task_status = task.get("status", task_status)
            priority = task.get("priority", priority)
            parent_task_id = task.get("parent_task_id", parent_task_id)
        if task_id is None:
            raise ValueError("task_id is required to publish an A2A message")

        message = A2AMessage(
            id=f"msg-{uuid.uuid4().hex}",
            created_at=_now(),
            kind=kind,
            from_agent=from_agent,
            to_agent=to_agent,
            task_id=task_id,
            task_title=task_title,
            task_status=task_status,
            priority=priority,
            parent_task_id=parent_task_id,
            summary=summary,
            payload=payload,
            attachments=attachments,
        )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(message.model_dump_json())
                f.write("\n")
        return message

    def list_messages(self, *, task_id: int | None = None, limit: int = 500) -> list[A2AMessage]:
        if not self.path.exists():
            return []
        items: list[A2AMessage] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                msg = A2AMessage.model_validate_json(line)
            except Exception:
                continue
            if task_id is not None and msg.task_id != task_id:
                continue
            items.append(msg)
        return items[-limit:]

    def task_summaries(self) -> list[dict[str, Any]]:
        latest: dict[int, dict[str, Any]] = {}
        for msg in self.list_messages(limit=5000):
            current = latest.get(msg.task_id)
            snapshot = {
                "task_id": msg.task_id,
                "title": msg.task_title,
                "status": msg.task_status,
                "priority": msg.priority,
                "parent_task_id": msg.parent_task_id,
                "last_message_at": msg.created_at.isoformat(),
                "last_summary": msg.summary,
                "message_count": (current or {}).get("message_count", 0) + 1,
            }
            latest[msg.task_id] = snapshot
        return sorted(
            latest.values(),
            key=lambda item: item["last_message_at"],
            reverse=True,
        )


store = A2ALogStore(config.A2A_MESSAGES_PATH)


def attachment(label: str, path: str | Path, media_type: str = "application/json") -> A2AAttachment:
    return A2AAttachment(label=label, path=str(path), media_type=media_type)


def read_json_file(path: str | Path) -> dict[str, Any] | list[Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
