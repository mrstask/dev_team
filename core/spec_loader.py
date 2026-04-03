"""Spec loader — reads agent specification markdown files from specs/."""
from pathlib import Path

import config

# Maps pipeline stage names → spec filename (without .md)
_STAGE_TO_SPEC: dict[str, str] = {
    "architect": "architect_spec",
    "develop":   "developer_spec",
    "developer": "developer_spec",
    "testing":   "tester_spec",
    "tester":    "tester_spec",
    "pm":        "pm_spec",
    "reviewer":  "reviewer_spec",
}

# The sections shown in the review command — enough to audit without full spec
_SUMMARY_SECTIONS = ("## Output Contract", "## Handoff Rules", "## Drift Indicators")


def load_spec(agent_name: str) -> str:
    """Return the full text of a spec file by agent name.

    agent_name can be a short name like 'architect', 'developer', 'pm',
    'reviewer', 'tester', or the exact filename stem like 'architect_spec'.
    Returns an empty string if the file is not found.
    """
    stem = _STAGE_TO_SPEC.get(agent_name, agent_name)
    path = config.SPECS_DIR / f"{stem}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def spec_summary_for_stage(stage: str) -> str:
    """Return the Output Contract + Handoff Rules + Drift Indicators sections
    for the given pipeline stage — used in the human review command.
    """
    full = load_spec(stage)
    if not full:
        return f"[dim]No spec found for stage '{stage}'. Check specs/ directory.[/dim]"

    lines = full.splitlines(keepends=True)
    result: list[str] = []
    capturing = False

    for line in lines:
        heading = line.strip()
        if any(heading.startswith(s) for s in _SUMMARY_SECTIONS):
            capturing = True
        elif heading.startswith("## ") and capturing:
            # Stop at the next top-level section that is not one of ours
            if not any(heading.startswith(s) for s in _SUMMARY_SECTIONS):
                capturing = False
        if capturing:
            result.append(line)

    return "".join(result).strip() or full[:1200]


def list_specs() -> list[str]:
    """Return names of all available spec files."""
    if not config.SPECS_DIR.exists():
        return []
    return [p.stem for p in sorted(config.SPECS_DIR.glob("*_spec.md"))]
