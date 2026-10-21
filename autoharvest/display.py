from __future__ import annotations

from typing import Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from autoharvest.models import (
    HarvestProject,
    HarvestUser,
    PagerDutyShift,
    PagerDutyUser,
    SyncPlan,
    SyncStatus,
)

console = Console()


def print_banner() -> None:
    banner = Text("autoharvest 🚜", style="bold green")
    banner.append(" - Sync PagerDuty Alerts to Harvest Time Tracking", style="dim white")
    console.print(banner)


def display_user_status(pd_user: PagerDutyUser, harvest_user: HarvestUser) -> None:
    table = Table(title="Connected Accounts", show_header=True, header_style="bold cyan")
    table.add_column("Service", style="bold")
    table.add_column("User Name")
    table.add_column("Email")
    table.add_column("ID / Timezone")

    table.add_row(
        "PagerDuty",
        pd_user.name,
        pd_user.email,
        f"{pd_user.id} ({pd_user.time_zone})",
    )
    table.add_row(
        "Harvest",
        harvest_user.full_name,
        harvest_user.email,
        f"ID: {harvest_user.id}",
    )
    console.print(table)


def display_projects_table(projects: list[HarvestProject]) -> None:
    table = Table(
        title="Available Harvest Projects & Tasks",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Project ID", style="cyan", justify="right")
    table.add_column("Project Name", style="bold")
    table.add_column("Task ID", style="green", justify="right")
    table.add_column("Task Name")

    for proj in projects:
        if not proj.tasks:
            table.add_row(str(proj.id), proj.name, "-", "[dim]No tasks assigned[/dim]")
        else:
            for idx, task in enumerate(proj.tasks):
                p_id_str = str(proj.id) if idx == 0 else ""
                p_name_str = proj.name if idx == 0 else ""
                table.add_row(p_id_str, p_name_str, str(task.id), task.name)

    console.print(table)


def display_shifts_table(shifts: list[PagerDutyShift], current_user_id: Optional[str] = None) -> None:
    table = Table(
        title="PagerDuty On-Call Shifts",
        show_header=True,
        header_style="bold blue",
    )
    table.add_column("Start Time", style="cyan")
    table.add_column("End Time", style="cyan")
    table.add_column("Duration", justify="right")
    table.add_column("Schedule", style="yellow")
    table.add_column("User ID", style="dim")

    for shift in shifts:
        duration = shift.end - shift.start
        duration_str = f"{duration.total_seconds() / 3600:.1f} hrs"
        start_str = shift.start.strftime("%Y-%m-%d %H:%M %Z")
        end_str = shift.end.strftime("%Y-%m-%d %H:%M %Z")
        sched_name = shift.schedule_summary or (shift.schedule_id or "Direct On-Call")

        user_tag = shift.user_id or ""
        if current_user_id and shift.user_id == current_user_id:
            user_tag = f"[bold green]{user_tag} (You)[/]"

        table.add_row(start_str, end_str, duration_str, sched_name, user_tag)

    console.print(table)


def display_sync_plan_table(plan: SyncPlan, is_dry_run: bool = True) -> None:
    date_range_str = (
        f"{plan.since.strftime('%Y-%m-%d %H:%M')} to {plan.until.strftime('%Y-%m-%d %H:%M %Z')}"
    )
    title = f"Sync Plan ({date_range_str})"
    table = Table(title=title, show_header=True, header_style="bold white on navy_blue")

    table.add_column("Status", justify="center")
    table.add_column("Ack Date/Time", style="cyan")
    table.add_column("Incident #", style="bold yellow", justify="right")
    table.add_column("Title / Summary")
    table.add_column("Service", style="magenta")
    table.add_column("Hours", justify="right", style="bold green")

    for item in plan.items:
        inc = item.incident
        ack_str = inc.acknowledged_at.strftime("%Y-%m-%d %H:%M")

        if item.status == SyncStatus.ALREADY_LOGGED:
            status_badge = f"[dim cyan]✓ Already Logged (ID {item.harvest_entry_id})[/]"
        elif item.status == SyncStatus.PENDING_CREATION:
            if is_dry_run:
                status_badge = "[bold yellow]⏳ Ready to Log[/]"
            else:
                status_badge = "[bold yellow]Creating...[/]"
        elif item.status == SyncStatus.CREATED:
            status_badge = f"[bold green]✓ Created (ID {item.harvest_entry_id})[/]"
        elif item.status == SyncStatus.FAILED:
            status_badge = f"[bold red]✗ Failed[/]"
        else:
            status_badge = str(item.status)

        table.add_row(
            status_badge,
            ack_str,
            str(inc.incident_number),
            inc.title,
            inc.service_name,
            f"{item.hours:.2f}h",
        )

    console.print(table)

    # Summary Panel
    total_count = len(plan.items)
    pending_count = len(plan.pending_items)
    already_count = len(plan.already_logged_items)
    pending_hours = plan.total_pending_hours
    already_hours = plan.total_logged_hours

    summary_text = (
        f"• [bold]Total Acknowledged Alerts:[/] {total_count}\n"
        f"• [bold cyan]Already Logged in Harvest:[/] {already_count} alert(s) ({already_hours:.2f} hours)\n"
        f"• [bold yellow]New Alerts to Log:[/] {pending_count} alert(s) ({pending_hours:.2f} hours)\n"
        f"• [bold]Harvest Destination:[/] Project ID [cyan]{plan.project_id}[/], Task ID [green]{plan.task_id}[/]\n"
        f"• [bold]Hours Per Alert:[/] {plan.hours_per_alert:.2f} hr(s)"
    )

    if is_dry_run:
        border_style = "yellow"
        title_text = "[bold yellow]DRY RUN PREVIEW (No changes made to Harvest)[/]"
    else:
        border_style = "green"
        title_text = "[bold green]SYNC RESULTS[/]"

    console.print(Panel(summary_text, title=title_text, border_style=border_style))
