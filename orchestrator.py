"""Board display utilities for task monitoring.

The interactive PM session has been replaced by the autonomous event loop
(event_loop.py). This module retains the board display for CLI monitoring.
"""
from rich.table import Table

import config
from clients import DashboardClient
from dtypes import Status

_db = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)

_PRIORITY_COLOR = {"critical": "red", "high": "yellow", "medium": "blue", "low": "dim"}
_STATUS_ORDER = Status.ALL


def show_board(status_filter: str | None = None) -> None:
    """Display task board grouped by status."""
    tasks = _db.get_tasks(status=status_filter)
    by_status: dict[str, list] = {}
    for t in tasks:
        by_status.setdefault(t["status"], []).append(t)

    for status in _STATUS_ORDER:
        items = by_status.get(status, [])
        if not items:
            continue
        tbl = Table(
            title=f"[bold]{status.upper()}[/bold] ({len(items)})",
            header_style="bold",
            show_lines=False,
        )
        tbl.add_column("ID", width=4)
        tbl.add_column("Title", min_width=42)
        tbl.add_column("Pri", width=9)
        tbl.add_column("Labels", width=30)
        tbl.add_column("Parent", width=6)
        for t in items:
            pc = _PRIORITY_COLOR.get(t["priority"], "white")
            labels = ", ".join(t.get("labels", []))
            parent = str(t["parent_task_id"]) if t.get("parent_task_id") else "-"
            tbl.add_row(
                str(t["id"]),
                t["title"][:55],
                f"[{pc}]{t['priority']}[/{pc}]",
                labels[:30],
                parent,
            )
        config.console.print(tbl)
