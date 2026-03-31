"""Tool implementations + Ollama function call specs for the dev agents."""
import subprocess
from pathlib import Path
from typing import Any

import config

# ── Ollama tool specs (function calling) ───────────────────────────────────────

TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file's full contents. "
                "Paths are relative to habr-agentic root. "
                "Use prefix 'habr_admin:' to read from the habr_admin source project, "
                "or 'lg_dashboard:' to read from the langgraph_dashboard project."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "File path. Examples: "
                            "'backend/app/models/article.py', "
                            "'habr_admin:backend/app/models/main.py', "
                            "'lg_dashboard:frontend/src/lib/api.ts'"
                        ),
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": (
                            "Glob pattern relative to project root. "
                            "Examples: 'backend/app/**/*.py', "
                            "'habr_admin:backend/app/etl/**/*.py', "
                            "'lg_dashboard:frontend/src/**/*.tsx'"
                        ),
                    }
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search for a text or regex pattern in code files (grep -rn).",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Text or regex to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Directory to search in (default: 'backend'). "
                            "Prefix with 'habr_admin:' or 'lg_dashboard:' for other projects."
                        ),
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_files",
            "description": (
                "Submit completed work. "
                "Write ALL files you created or modified. "
                "Call this ONCE when the task is fully implemented. "
                "Paths must be relative to habr-agentic root."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "description": "Files to write to the project",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Path relative to habr-agentic root",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Complete file content",
                                },
                            },
                            "required": ["path", "content"],
                        },
                    },
                    "summary": {
                        "type": "string",
                        "description": "What was implemented, key decisions made",
                    },
                },
                "required": ["files", "summary"],
            },
        },
    },
]


# ── Path resolution ────────────────────────────────────────────────────────────

def _resolve(path: str) -> tuple[Path, str]:
    """Return (base_dir, relative_path) after handling project prefixes."""
    if path.startswith("habr_admin:"):
        return config.HABR_ADMIN, path[len("habr_admin:"):]
    if path.startswith("lg_dashboard:"):
        return config.LANGGRAPH_DASHBOARD, path[len("lg_dashboard:"):]
    return config.ROOT, path


# ── Tool implementations ───────────────────────────────────────────────────────

_SKIP_DIRS = {"__pycache__", "node_modules", ".venv", ".git", ".mypy_cache", "dist", "build"}


def read_file(path: str) -> str:
    base, rel = _resolve(path)
    p = base / rel
    if not p.exists():
        return f"ERROR: File not found: {path}"
    if not p.is_file():
        return f"ERROR: Not a file: {path}"
    try:
        content = p.read_text(encoding="utf-8")
        if len(content) > 12_000:
            content = content[:12_000] + f"\n\n[...truncated — {len(content)} total chars]"
        return content
    except Exception as e:
        return f"ERROR reading {path}: {e}"


def list_files(pattern: str) -> str:
    base, rel_pattern = _resolve(pattern)
    matches = sorted(base.glob(rel_pattern))
    matches = [
        m for m in matches
        if m.is_file() and not any(part in _SKIP_DIRS for part in m.parts)
    ]
    if not matches:
        return "No files found."
    lines = [str(m.relative_to(base)) for m in matches[:60]]
    if len(matches) > 60:
        lines.append(f"... ({len(matches) - 60} more)")
    return "\n".join(lines)


def search_code(pattern: str, path: str = "backend") -> str:
    base, rel = _resolve(path)
    search_dir = base / rel
    if not search_dir.exists():
        return f"ERROR: Directory not found: {path}"
    try:
        result = subprocess.run(
            [
                "grep", "-rn",
                "--include=*.py", "--include=*.ts", "--include=*.tsx",
                "-l", pattern, str(search_dir),
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 1:
            return "No matches found."
        lines = result.stdout.strip().splitlines()
        relative = []
        for line in lines:
            try:
                relative.append(str(Path(line).relative_to(base)))
            except ValueError:
                relative.append(line)
        output = "\n".join(relative[:30])
        if len(lines) > 30:
            output += f"\n... ({len(lines) - 30} more)"
        return output
    except Exception as e:
        return f"ERROR: {e}"


def write_files(files: list[dict] | str, summary: str) -> dict:
    """Deferred — actual writing happens after PM review in orchestrator."""
    if isinstance(files, str):
        import json
        try:
            files = json.loads(files)
        except Exception:
            files = []
    return {"status": "pending_review", "files": files, "summary": summary}



# ── Dispatcher ─────────────────────────────────────────────────────────────────

def dispatch(name: str, args: dict) -> Any:
    if name == "read_file":
        return read_file(args.get("path", ""))
    if name == "list_files":
        return list_files(args.get("pattern", ""))
    if name == "search_code":
        return search_code(args.get("pattern", ""), args.get("path", "backend"))
    if name == "write_files":
        return write_files(args.get("files", []), args.get("summary", ""))
    return f"ERROR: Unknown tool '{name}'"

