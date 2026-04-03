"""Tool implementations + Ollama function call specs for the dev agents."""
import json
import shutil
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
                "Read a file's contents. Default reads up to 100000 chars — enough for most files in one call. "
                "NEVER use limit < 5000; always read as much as possible per call to avoid looping. "
                "If the file is still truncated after one call, use offset to read the next chunk. "
                "Paths are relative to the project root. "
                "Use prefix 'lg_dashboard:' to read from the langgraph_dashboard project."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "File path. Examples: "
                            "'backend/app/models/article.py', "
                            "'lg_dashboard:frontend/src/lib/api.ts'"
                        ),
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Character offset to start reading from (default: 0). Use when a previous read was truncated.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max characters to return (default: 100000). Never set below 5000.",
                    },
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
                            "Prefix with 'lg_dashboard:' for other projects."
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
            "name": "write_file",
            "description": (
                "Write a single file to disk. "
                "Call this once per file — do NOT bundle multiple files into one call. "
                "Paths must be relative to the project root — start with 'backend/', 'frontend/', etc. "
                "NEVER include the project name in paths (e.g. never 'habr-agentic/backend/...'). "
                "After writing ALL files, call finish() to complete the task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path relative to project root. "
                            "Examples: 'backend/app/models/article.py', "
                            "'backend/alembic/versions/0002_articles.py'. "
                            "NEVER start with the project folder name."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete file content",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tox",
            "description": (
                "Run the project test suite (tox if available, else pytest). "
                "Returns pass/fail status and the last 60 lines of output. "
                "Use this to verify your implementation before calling finish()."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": (
                "Signal that all files have been written and the task is complete. "
                "Call this ONCE after all write_file calls are done."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "What was implemented, key decisions made",
                    },
                },
                "required": ["summary"],
            },
        },
    },
]


# ── Path resolution ────────────────────────────────────────────────────────────

def _resolve(path: str) -> tuple[Path, str]:
    """Return (base_dir, relative_path) after handling project prefixes."""
    # Check registered source projects
    for prefix, base_path in config.SOURCE_PROJECTS.items():
        tag = f"{prefix}:"
        if path.startswith(tag):
            return base_path, path[len(tag):]
    if path.startswith("lg_dashboard:"):
        return config.LANGGRAPH_DASHBOARD, path[len("lg_dashboard:"):]
    return config.ROOT, path


# ── Tool implementations ───────────────────────────────────────────────────────

_SKIP_DIRS = {"__pycache__", "node_modules", ".venv", ".git", ".mypy_cache", "dist", "build"}


def read_file(path: str, offset: int = 0, limit: int = 100_000) -> str:
    base, rel = _resolve(path)
    p = base / rel
    if not p.exists():
        return f"ERROR: File not found: {path}"
    if not p.is_file():
        return f"ERROR: Not a file: {path}"
    try:
        content = p.read_text(encoding="utf-8")
        total = len(content)
        chunk = content[offset:offset + limit]
        remaining = total - offset - len(chunk)
        suffix = (
            f"\n\n[...truncated — showing chars {offset}–{offset + len(chunk)} of {total}. "
            f"Call read_file(path='{path}', offset={offset + len(chunk)}) to read the next chunk.]"
            if remaining > 0 else ""
        )
        return chunk + suffix
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


def write_file(path: str, content: str) -> str:
    """Write a single file to disk. Returns a status string."""
    if not path:
        return "ERROR: path is required"
    # Strip accidental project-name prefix (e.g. "habr-agentic/backend/...")
    root_name = config.ROOT.name
    if path.startswith(root_name + "/"):
        path = path[len(root_name) + 1:]
    try:
        target = config.ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"OK: wrote {path}"
    except Exception as e:
        return f"ERROR writing {path}: {e}"


# Accumulator for files written via write_file() in the current loop iteration
_written_files: list[dict] = []


def finish(summary: str) -> dict:
    """Signal task completion — collects all write_file() calls made this turn."""
    files = list(_written_files)
    _written_files.clear()
    return {"status": "pending_review", "files": files, "summary": summary, "written": [f["path"] for f in files]}


def write_files(files: list[dict] | str, summary: str) -> dict:
    """Legacy bulk write — kept for backward compat. Writes all files then signals done."""
    if isinstance(files, str):
        try:
            files = json.loads(files)
        except Exception as e:
            config.console.print(f"[red]  write_files: JSON parse failed ({e}) — 0 files written[/red]")
            files = []

    written: list[str] = []
    errors: list[str] = []
    for f in files:
        path = f.get("path", "")
        content = f.get("content", "")
        if not path:
            continue
        root_name = config.ROOT.name
        if path.startswith(root_name + "/"):
            path = path[len(root_name) + 1:]
        try:
            target = config.ROOT / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(path)
        except Exception as e:
            errors.append(f"{path}: {e}")

    if errors:
        config.console.print(f"[red]  write_files errors: {'; '.join(errors)}[/red]")

    return {"status": "pending_review", "files": files, "summary": summary, "written": written}



def run_tox() -> str:
    """Run tox (or pytest fallback). Returns status + last 60 lines of output."""
    tox_bin = shutil.which("tox")

    def _find_pytest() -> Path | None:
        candidates = [
            config.BACKEND / ".venv" / "bin" / "pytest",
            config.BACKEND / "venv" / "bin" / "pytest",
            *sorted((config.ROOT / ".tox").glob("*/bin/pytest")),
        ]
        return next((p for p in candidates if p.exists()), None)

    if tox_bin:
        cmd = [tox_bin]
        cwd = str(config.ROOT)
        label = "tox"
    else:
        found = _find_pytest()
        pytest_bin = str(found) if found else (shutil.which("pytest") or "pytest")
        cmd = [pytest_bin, "tests/", "-v", "--tb=short"]
        cwd = str(config.BACKEND)
        label = f"pytest ({pytest_bin})"

    config.console.print(f"\n[dim]  run_tox: running {label} ...[/dim]")
    try:
        process = subprocess.Popen(
            cmd, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        output = ""
        for line in process.stdout:
            output += line
            config.console.print(f"  [dim]{line.rstrip()}[/dim]")
        process.wait(timeout=300)
        last_lines = "\n".join(output.splitlines()[-60:])
        status = "PASSED" if process.returncode == 0 else "FAILED"
        return f"tox {status}\n\n{last_lines}"
    except FileNotFoundError:
        return f"ERROR: '{cmd[0]}' not found. Install tox or pytest in the backend venv."
    except Exception as e:
        return f"ERROR: {e}"


# ── Dispatcher ─────────────────────────────────────────────────────────────────

def dispatch(name: str, args: dict) -> Any:
    if name == "read_file":
        limit = max(5_000, int(args.get("limit", 100_000)))
        return read_file(args.get("path", ""), int(args.get("offset", 0)), limit)
    if name == "list_files":
        return list_files(args.get("pattern", ""))
    if name == "search_code":
        return search_code(args.get("pattern", ""), args.get("path", "backend"))
    if name == "write_file":
        path = args.get("path", "")
        content = args.get("content", "")
        result = write_file(path, content)
        if result.startswith("OK:"):
            _written_files.append({"path": path, "content": content})
        return result
    if name == "finish":
        return finish(args.get("summary", ""))
    if name == "write_files":
        return write_files(args.get("files", []), args.get("summary", ""))
    if name == "run_tox":
        return run_tox()
    if name == "run_tests":
        return "ERROR: Use run_tox instead — it runs tox (or pytest) and returns output."
    return f"ERROR: Unknown tool '{name}'"

