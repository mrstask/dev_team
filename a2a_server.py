"""Minimal A2A gateway so the external A2A Inspector can inspect Dev Team tasks."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import config
from a2a_bridge import read_json_file, store
from clients import DashboardClient
from dtypes import Action, Status

_db = DashboardClient(config.DASHBOARD_URL)


def run_a2a_server(host: str = config.A2A_DEFAULT_HOST, port: int = config.A2A_DEFAULT_PORT) -> None:
    server = ThreadingHTTPServer((host, port), _build_handler(host, port))
    config.console.print(
        f"[bold cyan]A2A gateway[/bold cyan] listening on http://{host}:{port}\n"
        f"  Agent card: http://{host}:{port}/.well-known/agent.json\n"
        f"  RPC URL:    http://{host}:{port}/a2a\n"
        f"  Inspector:  point A2A Inspector at the agent card or base URL"
    )
    server.serve_forever()


def _build_handler(host: str, port: int):
    base_url = f"http://{host}:{port}"

    class Handler(BaseHTTPRequestHandler):
        server_version = "DevTeamA2A/0.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_json({
                    "name": "dev-team-a2a-gateway",
                    "agent_card_url": f"{base_url}/.well-known/agent.json",
                    "rpc_url": f"{base_url}/a2a",
                })
                return
            if parsed.path in ("/.well-known/agent.json", "/.well-known/agent-card.json"):
                self._send_json(_agent_card(base_url))
                return
            if parsed.path == "/a2a":
                self._send_json(_agent_card(base_url))
                return
            if parsed.path == "/v1/tasks":
                try:
                    tasks = [_build_task_snapshot(t["id"]) for t in _db.get_tasks()]
                    self._send_json(tasks)
                except Exception as exc:
                    self._send_error_json(HTTPStatus.BAD_GATEWAY, str(exc))
                return
            if parsed.path.startswith("/v1/tasks/"):
                task_id = parsed.path.split("/")[-1]
                query = parse_qs(parsed.query)
                history_length = _safe_int(query.get("historyLength", [None])[0])
                try:
                    self._send_json(_build_task_snapshot(int(task_id), history_length=history_length))
                except Exception as exc:
                    self._send_error_json(HTTPStatus.BAD_GATEWAY, str(exc))
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, f"Unknown path: {parsed.path}")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/a2a":
                self._send_error_json(HTTPStatus.NOT_FOUND, f"Unknown path: {parsed.path}")
                return
            req = self._read_json()
            if not isinstance(req, dict):
                self._send_jsonrpc_error(None, -32700, "Invalid JSON body")
                return

            rpc_id = req.get("id")
            method = req.get("method")
            params = req.get("params", {}) or {}

            try:
                if method == "message/send":
                    result = _handle_message_send(params)
                elif method == "tasks/get":
                    result = _handle_tasks_get(params)
                elif method == "tasks/list":
                    result = [_build_task_snapshot(t["id"]) for t in _db.get_tasks()]
                elif method == "tasks/cancel":
                    result = _handle_tasks_cancel(params)
                else:
                    self._send_jsonrpc_error(rpc_id, -32601, f"Method not found: {method}")
                    return
            except ValueError as exc:
                self._send_jsonrpc_error(rpc_id, -32004, str(exc))
                return
            except Exception as exc:
                self._send_jsonrpc_error(rpc_id, -32000, str(exc))
                return

            self._send_json({"jsonrpc": "2.0", "id": rpc_id, "result": result})

        def _read_json(self) -> Any:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))

        def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, status: HTTPStatus, message: str) -> None:
            self._send_json({"error": message}, status=status)

        def _send_jsonrpc_error(self, rpc_id: Any, code: int, message: str) -> None:
            self._send_json({
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": code, "message": message},
            })

        def log_message(self, format: str, *args) -> None:
            return

    return Handler


def _agent_card(base_url: str) -> dict[str, Any]:
    return {
        "protocolVersion": "0.2.6",
        "name": "Dev Team Gateway",
        "description": (
            "A gateway over the autonomous Dev Team pipeline. "
            "Use it with A2A Inspector to create tasks and inspect inter-agent handoffs."
        ),
        "url": f"{base_url}/a2a",
        "preferredTransport": "JSONRPC",
        "version": "0.1.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": [
            {
                "id": "software-delivery",
                "name": "Software Delivery",
                "description": "Turns a software request into a Dev Team dashboard task and exposes agent handoffs.",
                "tags": ["software", "tasks", "a2a", "debugging"],
                "examples": [
                    "Implement a CLI feature and show me which agents touched it.",
                    "Create a task for a bug fix and inspect the architect/developer/tester handoffs.",
                ],
            }
        ],
    }


def _handle_message_send(params: dict[str, Any]) -> dict[str, Any]:
    message = params.get("message") or {}
    parts = message.get("parts") or []
    text = "\n".join(
        part.get("text", "")
        for part in parts
        if part.get("kind") == "text" or ("text" in part and isinstance(part.get("text"), str))
    ).strip()
    if not text:
        raise ValueError("message/send requires at least one text part")

    task_id = message.get("taskId")
    history_length = ((params.get("configuration") or {}).get("historyLength")) or 50

    if task_id:
        task = _db.get_task(int(task_id))
        if task["status"] in (Status.DONE, Status.FAILED):
            raise ValueError(f"Task {task_id} is terminal and cannot be restarted")
        fresh = _db.get_task(task["id"])
        _db.append_review_feedback(task["id"], fresh, {
            "issues": [text],
            "overall_comment": "A2A client note",
        })
        store.publish(
            task=task,
            from_agent="a2a:client",
            to_agent="dev-team",
            kind="request",
            summary=text[:200],
            payload={"message": text, "continued_task": True},
        )
        return _build_task_snapshot(task["id"], history_length=history_length)

    title = text.splitlines()[0][:120] or "A2A task"
    new_task_id = _db.create_task(
        title=title,
        description=text,
        status=Status.ARCHITECT,
        priority="medium",
        labels=[Action.TODO, "a2a"],
        project_id=_default_project_id(),
    )
    task = _db.get_task(new_task_id)
    store.publish(
        task=task,
        from_agent="a2a:client",
        to_agent="researcher:explore",
        kind="request",
        summary=text[:200],
        payload={"message": text, "created_via": "a2a"},
    )
    return _build_task_snapshot(new_task_id, history_length=history_length)


def _handle_tasks_get(params: dict[str, Any]) -> dict[str, Any]:
    task_id = params.get("id")
    if task_id is None:
        raise ValueError("tasks/get requires params.id")
    return _build_task_snapshot(int(task_id), history_length=_safe_int(params.get("historyLength")))


def _handle_tasks_cancel(params: dict[str, Any]) -> dict[str, Any]:
    task_id = params.get("id")
    if task_id is None:
        raise ValueError("tasks/cancel requires params.id")
    task = _db.get_task(int(task_id))
    if task["status"] not in (Status.DONE, Status.FAILED):
        _db.move_task(task["id"], Status.FAILED)
        task = _db.get_task(task["id"])
        store.publish(
            task=task,
            from_agent="a2a:client",
            to_agent="dev-team",
            kind="decision",
            summary="Task canceled from A2A client",
            payload={"canceled": True},
        )
    return _build_task_snapshot(task["id"])


def _build_task_snapshot(task_id: int, history_length: int | None = None) -> dict[str, Any]:
    task = _db.get_task(task_id)
    history_length = history_length or 50
    context_id = f"dev-team-task-{task_id}"
    messages = store.list_messages(task_id=task_id, limit=history_length)
    latest_message = messages[-1] if messages else None
    latest_text = latest_message.summary if latest_message else f"{task['status']} ({', '.join(task.get('labels', []))})"

    snapshot = {
        "id": str(task["id"]),
        "contextId": context_id,
        "kind": "task",
        "status": {
            "state": _map_task_state(task),
            "timestamp": task.get("updated_at") or datetime.now(timezone.utc).isoformat(),
            "message": _agent_message(
                text=latest_text,
                task_id=task["id"],
                context_id=context_id,
                metadata={"status": task["status"], "labels": task.get("labels", [])},
            ),
        },
        "history": [_log_to_a2a_message(msg, context_id) for msg in messages],
        "artifacts": _build_artifacts(task, messages),
        "metadata": {
            "taskTitle": task.get("title", ""),
            "taskStatus": task.get("status", ""),
            "priority": task.get("priority", ""),
            "labels": task.get("labels", []),
            "parentTaskId": task.get("parent_task_id"),
        },
    }
    return snapshot


def _build_artifacts(task: dict, messages: list) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    task_id = task["id"]

    communications = [
        {
            "at": msg.created_at.isoformat(),
            "kind": msg.kind,
            "from": msg.from_agent,
            "to": msg.to_agent,
            "summary": msg.summary,
            "payload": msg.payload,
            "attachments": [item.model_dump() for item in msg.attachments],
        }
        for msg in messages
    ]
    artifacts.append({
        "artifactId": f"communications-{task_id}",
        "name": "Agent communications",
        "description": "Structured handoffs and review messages captured inside the Dev Team pipeline.",
        "parts": [{
            "kind": "data",
            "data": {"messages": communications},
            "metadata": {"mediaType": "application/json"},
        }],
    })

    for key in ("research", "architect", "developer", "testing", "feedback"):
        path = config.CONTEXT_DIR / str(task_id) / f"{key}.json"
        data = read_json_file(path)
        if data is None:
            continue
        artifacts.append({
            "artifactId": f"{key}-{task_id}",
            "name": f"{key}.json",
            "description": f"Current persisted {key} context for task {task_id}.",
            "parts": [{
                "kind": "data",
                "data": data if isinstance(data, dict) else {"items": data},
                "metadata": {"mediaType": "application/json", "path": str(path)},
            }],
        })

    return artifacts


def _map_task_state(task: dict) -> str:
    status = task.get("status")
    labels = task.get("labels", [])
    if status == Status.DONE:
        return "completed"
    if status == Status.FAILED:
        return "failed"
    if Action.AWAIT_HUMAN in labels:
        return "input-required"
    if status in (Status.ARCHITECT, Status.DEVELOP, Status.TESTING):
        return "working"
    return "submitted"


def _log_to_a2a_message(msg, context_id: str) -> dict[str, Any]:
    lines = [msg.summary] if msg.summary else []
    if msg.payload:
        lines.append(json.dumps(msg.payload, ensure_ascii=False, indent=2))
    return {
        "role": "agent" if msg.from_agent != "a2a:client" else "user",
        "parts": [{"kind": "text", "text": "\n\n".join(lines) or "(no content)"}],
        "messageId": msg.id,
        "taskId": str(msg.task_id),
        "contextId": context_id,
        "kind": "message",
        "metadata": {
            "kind": msg.kind,
            "fromAgent": msg.from_agent,
            "toAgent": msg.to_agent,
            "attachments": [item.model_dump() for item in msg.attachments],
        },
    }


def _agent_message(text: str, task_id: int, context_id: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "role": "agent",
        "parts": [{"kind": "text", "text": text}],
        "messageId": f"status-{uuid.uuid4().hex}",
        "taskId": str(task_id),
        "contextId": context_id,
        "kind": "message",
        "metadata": metadata or {},
    }


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _default_project_id() -> int:
    try:
        project_ids = [p["id"] for p in _db.get_projects() if p.get("id") is not None]
    except Exception:
        return 3
    if 3 in project_ids:
        return 3
    if not project_ids:
        raise ValueError("No dashboard projects are available for A2A task creation")
    return project_ids[0]
