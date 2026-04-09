"""Tool implementations + Ollama function call specs for the dev agents."""
import json
import shutil
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import config

# ── Per-task project context (thread-local) ──────────────────────────────────

_local = threading.local()


def set_project_root(root: Path) -> None:
    """Set the target project root for the current task."""
    _local.project_root = root


def get_project_root() -> Path:
    """Return the current task's project root. Falls back to config.ROOT if unset."""
    return getattr(_local, "project_root", None) or config.ROOT


def clear_project_root() -> None:
    """Clear the per-task project root."""
    _local.project_root = None


@contextmanager
def project_context(root: Path):
    """Context manager that sets and clears the project root for a task."""
    set_project_root(root)
    try:
        yield root
    finally:
        clear_project_root()

# ── Scoped file accumulator ───────────────────────────────────────────────────

def _get_written_files() -> list[dict]:
    if not hasattr(_local, "written_files"):
        _local.written_files = []
    return _local.written_files


def _get_written_paths() -> list[Path]:
    """Return the list of absolute paths written to disk in the current scope."""
    if not hasattr(_local, "written_paths"):
        _local.written_paths = []
    return _local.written_paths


def get_written_paths() -> list[Path]:
    """Public accessor — returns a copy of absolute paths written in the current tool_scope."""
    return list(_get_written_paths())


@contextmanager
def tool_scope():
    """Context manager that ensures written_files is cleared after use.

    Wraps every run_react_loop() invocation so that files from a failed/stalled
    task never leak into the next task's output.
    """
    _get_written_files().clear()
    _get_written_paths().clear()
    try:
        yield
    finally:
        _get_written_files().clear()
        _get_written_paths().clear()


# ── Ollama tool specs (function calling) ───────────────────────────────────────

TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file's contents (up to 100 000 chars). "
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
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "List files matching a glob pattern. "
                "IMPORTANT: '**' alone matches only directories — "
                "always end with a filename pattern like '**/*.py' or '**/*'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": (
                            "Glob pattern relative to project root. "
                            "ALWAYS use **/*.ext or **/* — never bare **. "
                            "Examples: 'backend/app/**/*.py', 'backend/**/*', "
                            "'*.py', 'lg_dashboard:frontend/src/**/*.tsx'"
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
            "description": "Search for a text or regex pattern in code files (grep -rn). Returns matching file paths.",
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
            "name": "run_pytest",
            "description": (
                "Run pytest tests. "
                "Returns pass/fail status and failed test output only. "
                "Use this to verify your implementation before calling finish(). "
                "Max 2 attempts — then call finish() regardless. "
                "Pass a specific test file path to run only that file instead of the full suite."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Optional test file path relative to backend/, "
                            "e.g. 'tests/test_pipeline_utils.py'. "
                            "If omitted, runs the full test suite (tests/)."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_pylint",
            "description": (
                "Run pylint on the backend source. "
                "Much faster than the full test suite. Use this after fixing lint errors "
                "instead of re-running all tests."
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
    return get_project_root(), path


# ── Tool implementations ───────────────────────────────────────────────────────

_SKIP_DIRS = {"__pycache__", "node_modules", ".venv", ".git", ".mypy_cache", "dist", "build"}


def read_file(path: str) -> str:
    _MAX_CHARS = 100_000
    base, rel = _resolve(path)
    p = base / rel
    if not p.exists():
        return f"ERROR: File not found: {path}"
    if not p.is_file():
        return f"ERROR: Not a file: {path}"
    try:
        content = p.read_text(encoding="utf-8")
        if len(content) > _MAX_CHARS:
            return content[:_MAX_CHARS] + f"\n\n[...truncated at {_MAX_CHARS} of {len(content)} chars]"
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


def write_file(path: str, content: str) -> str:
    """Write a single file to disk. Returns a status string."""
    if not path:
        return "ERROR: path is required"
    root = get_project_root()
    # Strip accidental project-name prefix (e.g. "habr-agentic/backend/...")
    root_name = root.name
    if path.startswith(root_name + "/"):
        path = path[len(root_name) + 1:]
    try:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        _get_written_paths().append(target)
        return f"OK: wrote {path}"
    except Exception as e:
        return f"ERROR writing {path}: {e}"


def finish(summary: str) -> dict:
    """Signal task completion — collects all write_file() calls made this turn."""
    wf = _get_written_files()
    files = list(wf)
    wf.clear()
    return {"status": "pending_review", "files": files, "summary": summary, "written": [f["path"] for f in files]}


def write_files(files: list[dict] | str, summary: str) -> dict:
    """Legacy bulk write — kept for backward compat. Writes all files then signals done."""
    if isinstance(files, str):
        try:
            files = json.loads(files)
        except Exception as e:
            config.console.print(f"[red]  write_files: JSON parse failed ({e}) — 0 files written[/red]")
            files = []

    root = get_project_root()
    written: list[str] = []
    errors: list[str] = []
    for f in files:
        path = f.get("path", "")
        content = f.get("content", "")
        if not path:
            continue
        root_name = root.name
        if path.startswith(root_name + "/"):
            path = path[len(root_name) + 1:]
        try:
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(path)
        except Exception as e:
            errors.append(f"{path}: {e}")

    if errors:
        config.console.print(f"[red]  write_files errors: {'; '.join(errors)}[/red]")

    return {"status": "pending_review", "files": files, "summary": summary, "written": written}



def _find_venv_bin(name: str) -> str:
    backend = get_project_root() / "backend"
    candidates = [
        backend / ".venv" / "bin" / name,
        backend / "venv" / "bin" / name,
    ]
    found = next((p for p in candidates if p.exists()), None)
    return str(found) if found else (shutil.which(name) or name)


def _run_tool_subprocess(cmd: list[str], cwd: str, label: str, timeout: int = 300) -> str:
    config.console.print(f"\n[dim]  running {label} ...[/dim]")
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
        process.wait(timeout=timeout)
        last_lines = "\n".join(output.splitlines()[-60:])
        status = "PASSED" if process.returncode == 0 else "FAILED"
        return f"{label} {status}\n\n{last_lines}"
    except FileNotFoundError:
        return f"ERROR: '{cmd[0]}' not found."
    except Exception as e:
        return f"ERROR: {e}"


def run_pytest(path: str = "") -> str:
    """Run pytest showing only failures (--tb=short -q). Returns status + output.

    Args:
        path: Optional test file path relative to backend/ (e.g. 'tests/test_foo.py').
              If empty, runs the full test suite.
    """
    pytest_bin = _find_venv_bin("pytest")
    backend = get_project_root() / "backend"
    target = path if path else "tests/"
    label = f"pytest {target}" if path else "pytest"
    cmd = [pytest_bin, target, "--tb=short", "-q"]
    return _run_tool_subprocess(cmd, str(backend), label)


def run_pylint() -> str:
    """Run pylint on the backend source. Returns status + output."""
    pylint_bin = _find_venv_bin("pylint")
    backend = get_project_root() / "backend"
    backend_src = backend / "app"
    target = str(backend_src) if backend_src.exists() else str(backend)
    cmd = [pylint_bin, target, "--output-format=text", "--score=no"]
    return _run_tool_subprocess(cmd, str(backend), "pylint", timeout=120)


# ── Dispatcher ─────────────────────────────────────────────────────────────────

def dispatch(name: str, args: dict) -> Any:
    if name == "read_file":
        return read_file(args.get("path", ""))
    if name == "list_files":
        return list_files(args.get("pattern", ""))
    if name == "search_code":
        return search_code(args.get("pattern", ""), args.get("path", "backend"))
    if name == "write_file":
        path = args.get("path", "")
        content = args.get("content", "")
        result = write_file(path, content)
        if result.startswith("OK:"):
            _get_written_files().append({"path": path, "content": content})
        return result
    if name == "finish":
        return finish(args.get("summary", ""))
    if name == "write_files":
        return write_files(args.get("files", []), args.get("summary", ""))
    if name == "run_pytest":
        return run_pytest(args.get("path", ""))
    if name == "run_pylint":
        return run_pylint()
    if name in ("run_tox", "run_tox_lint", "run_tests"):
        return "ERROR: Use run_pytest (tests) or run_pylint (lint) instead."
    if name == "submit_research":
        return submit_research(args.get("findings", ""))
    return f"ERROR: Unknown tool '{name}'"


# ── Research tools ─────────────────────────────────────────────────────────────

def submit_research(findings: str) -> dict:
    """Terminal tool for ResearchAgent — wraps findings in a pending_review envelope."""
    return {"status": "pending_review", "files": [], "summary": findings, "written": []}


# ── Tool subsets per agent role ────────────────────────────────────────────────

_ARCHITECT_EXCLUDED_TOOLS = {"run_pytest", "run_pylint"}

ARCHITECT_TOOL_SPECS: list[dict] = [
    s for s in TOOL_SPECS if s["function"]["name"] not in _ARCHITECT_EXCLUDED_TOOLS
]

_RESEARCH_TOOL_NAMES = {"read_file", "list_files", "search_code"}

RESEARCH_TOOL_SPECS: list[dict] = [
    s for s in TOOL_SPECS if s["function"]["name"] in _RESEARCH_TOOL_NAMES
] + [
    {
        "type": "function",
        "function": {
            "name": "submit_research",
            "description": (
                "Submit your research findings. Call this when you have gathered enough "
                "information about the codebase. Do NOT call any other tool after this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "findings": {
                        "type": "string",
                        "description": (
                            'JSON string with keys: "relevant_files" (list[str]), '
                            '"patterns" (list[str]), "data_flow" (str), '
                            '"warnings" (list[str]), "summary" (str)'
                        ),
                    }
                },
                "required": ["findings"],
            },
        },
    },
]

