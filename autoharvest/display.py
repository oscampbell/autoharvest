from __future__ import annotations

from datetime import timedelta
from typing import Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from autoharvest.models import (
    EstimatedPayBreakdown,
    HarvestProject,
    HarvestUser,
    OvertimeReport,
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
    table.add_column("Alert Time", style="cyan")
    table.add_column("Incident Ref", style="bold yellow")
    table.add_column("Title / Summary")
    table.add_column("Service", style="magenta")
    table.add_column("Hours", justify="right", style="bold green")

    for item in plan.items:
        inc = item.incident
        alert_time_str = inc.event_time.strftime("%Y-%m-%d %H:%M")
        if item.booked_start is not None and item.status in (
            SyncStatus.PENDING_CREATION, SyncStatus.CREATED, SyncStatus.FAILED
        ):
            booked_end = item.booked_start + timedelta(hours=item.hours)
            alert_time_str += f"\n[dim]→ {item.booked_start.strftime('%H:%M')}–{booked_end.strftime('%H:%M')}[/]"
        inc_ref = f"PG#{inc.incident_number}"

        if item.status == SyncStatus.ALREADY_LOGGED:
            status_badge = f"[dim cyan]✓ Already Logged (ID {item.harvest_entry_id})[/]"
            hours_str = f"[dim]{item.hours:.2f}h[/]"
        elif item.status == SyncStatus.IGNORED_WINDOW:
            by_inc = item.ignored_by_incident
            by_ref = f"PG#{by_inc.incident_number}" if by_inc else "prior alert"
            win_str = "<30m" if item.window_hours == 0.5 else (f"<{item.window_hours:.1f}h" if item.window_hours != 1.0 else "<1h")
            status_badge = f"[dim]⚡ Ignored ({win_str} since {by_ref})[/]"
            hours_str = "[dim]0.00h[/]"
        elif item.status == SyncStatus.IGNORED_NOT_ONCALL:
            status_badge = "[dim]🚫 Not On-Call (Outside shift)[/]"
            hours_str = "[dim]0.00h[/]"
        elif item.status == SyncStatus.PENDING_CREATION:
            if is_dry_run:
                status_badge = "[bold yellow]⏳ Ready to Log[/]"
            else:
                status_badge = "[bold yellow]Creating...[/]"
            hours_str = f"[bold green]{item.hours:.2f}h[/]"
        elif item.status == SyncStatus.CREATED:
            status_badge = f"[bold green]✓ Created (ID {item.harvest_entry_id})[/]"
            hours_str = f"[bold green]{item.hours:.2f}h[/]"
        elif item.status == SyncStatus.FAILED:
            status_badge = "[bold red]✗ Failed[/]"
            hours_str = f"[bold red]{item.hours:.2f}h[/]"
        else:
            status_badge = str(item.status)
            hours_str = f"{item.hours:.2f}h"

        table.add_row(
            status_badge,
            alert_time_str,
            inc_ref,
            inc.title,
            inc.service_name,
            hours_str,
        )

    console.print(table)

    # Summary Panel
    total_count = len(plan.items)
    pending_count = len(plan.pending_items)
    already_count = len(plan.already_logged_items)
    ignored_count = len(plan.ignored_items)
    pending_hours = plan.total_pending_hours
    already_hours = plan.total_logged_hours
    bookable_count = pending_count + already_count
    total_bookable_hours = pending_hours + already_hours

    summary_lines = []
    if plan.shifts:
        summary_lines.append(f"• [bold]Scheduled On-Call Shifts Found:[/] {len(plan.shifts)} shift(s)")
    summary_lines.extend([
        f"• [bold green]Bookable On-Call Alerts:[/] {bookable_count} alert(s) ({total_bookable_hours:.2f} hours)",
        f"  ├─ [bold cyan]Already Logged in Harvest:[/] {already_count} alert(s) ({already_hours:.2f} hours)",
        f"  └─ [bold yellow]New Alerts to Log:[/] {pending_count} alert(s) ({pending_hours:.2f} hours)",
    ])
    if pending_count > 0:
        sociable_pending = [i for i in plan.pending_items if i.is_sociable]
        unsociable_pending = [i for i in plan.pending_items if not i.is_sociable]
        summary_lines.extend([
            f"      ├─ [green]Sociable Hours ({plan.sociable_hours:.2f}h):[/] {len(sociable_pending)} alert(s) ({sum(i.hours for i in sociable_pending):.2f} hrs)",
            f"      └─ [green]Unsociable Hours ({plan.unsociable_hours:.2f}h):[/] {len(unsociable_pending)} alert(s) ({sum(i.hours for i in unsociable_pending):.2f} hrs)",
        ])
    summary_lines.append(
        f"• [dim]Ignored Alerts (Within active alert window):[/] {ignored_count} alert(s) (0.00 hours)"
    )
    if plan.non_oncall_ignored_count > 0:
        summary_lines.append(
            f"• [dim]Ignored Alerts (Outside Scheduled On-Call Shifts):[/] {plan.non_oncall_ignored_count} alert(s)"
        )
    summary_lines.extend([
        f"• [bold]Harvest Destination:[/] Project ID [cyan]{plan.project_id}[/], Task ID [green]{plan.task_id}[/]",
        f"• [bold]Naming Convention:[/] [italic]PG#IncidentNo. - Impacted Service - Summary[/]",
    ])

    summary_text = "\n".join(summary_lines)

    if is_dry_run:
        border_style = "yellow"
        title_text = "[bold yellow]DRY RUN PREVIEW (No changes made to Harvest)[/]"
    else:
        border_style = "green"
        title_text = "[bold green]SYNC RESULTS[/]"

    console.print(Panel(summary_text, title=title_text, border_style=border_style))


def display_overtime_report(report: OvertimeReport, detailed: bool = False) -> None:
    """Display a rich summary table and breakdown of earned overtime."""
    cur = report.currency_symbol

    # Category Breakdown Table
    table = Table(
        title=f"Earned Overtime Summary — {report.period_label} ({report.start_date} to {report.end_date})",
        show_header=True,
        header_style="bold white on dark_green",
    )
    table.add_column("Category / Task", style="bold")
    table.add_column("Project", style="dim")
    table.add_column("Entries", justify="right")
    table.add_column("Hours", justify="right", style="cyan")
    table.add_column("Hourly Rate", justify="right", style="yellow")
    table.add_column("Total Earned", justify="right", style="bold green")

    for cat in report.categories:
        table.add_row(
            cat.category,
            cat.project_name,
            str(cat.entry_count),
            f"{cat.total_hours:.2f} hrs",
            f"{cur}{cat.hourly_rate:.2f}/hr",
            f"{cur}{cat.total_earned:,.2f}",
        )

    # Total row
    table.add_section()
    table.add_row(
        "[bold]TOTAL OVERTIME[/]",
        "-",
        f"[bold]{len(report.items)}[/]",
        f"[bold cyan]{report.total_hours:.2f} hrs[/]",
        "-",
        f"[bold green]{cur}{report.total_earned:,.2f}[/]",
    )

    console.print(table)

    # Detailed table if requested
    if detailed and report.items:
        console.print("")
        detail_table = Table(
            title=f"Detailed Overtime Log Entries — {report.period_label}",
            show_header=True,
            header_style="bold white on navy_blue",
        )
        detail_table.add_column("Date", style="cyan")
        detail_table.add_column("Category / Task", style="bold")
        detail_table.add_column("Hours", justify="right", style="green")
        detail_table.add_column("Rate", justify="right", style="yellow")
        detail_table.add_column("Earned", justify="right", style="bold green")
        detail_table.add_column("Notes / Summary", style="dim")

        for item in report.items:
            e = item.entry
            note_str = (e.notes or "-").replace("\r\n", " ").replace("\n", " ").strip()
            if len(note_str) > 70:
                note_str = note_str[:67] + "..."
            detail_table.add_row(
                e.spent_date,
                item.category,
                f"{e.hours:.2f}h",
                f"{cur}{item.hourly_rate:.2f}",
                f"{cur}{item.total_earned:,.2f}",
                note_str,
            )

        console.print(detail_table)

    # Summary Panel
    summary_lines = [
        f"• [bold]Overtime Period:[/] {report.start_date} to {report.end_date} ([bold cyan]{report.period_label}[/])",
        f"• [bold]Total Overtime Logged:[/] [bold cyan]{report.total_hours:.2f} hours[/] across {len(report.items)} entries",
    ]
    for cat in report.categories:
        summary_lines.append(
            f"  ├─ [bold]{cat.category}:[/] {cat.total_hours:.2f} hrs @ {cur}{cat.hourly_rate:.2f}/hr = [green]{cur}{cat.total_earned:,.2f}[/]"
        )

    summary_lines.extend([
        "",
        f"💰 [bold white on dark_green] TOTAL EARNED OVERTIME: {cur}{report.total_earned:,.2f} [/]",
    ])

    summary_text = "\n".join(summary_lines)
    console.print(Panel(summary_text, title=f"[bold green]OVERTIME EARNINGS — {report.period_label}[/]", border_style="green"))


def display_estimated_pay(pay: EstimatedPayBreakdown, period_label: str) -> None:
    """Display estimated take-home pay, comparing base salary, overtime deductions, and overall net pay."""
    cur = pay.currency_symbol

    # Table comparing Base Salary, Overtime, and Total Month
    table = Table(
        title=f"Estimated Monthly Pay Breakdown — {period_label}",
        show_header=True,
        header_style="bold white on navy_blue",
    )
    table.add_column("Component", style="bold")
    table.add_column("Base Salary", justify="right", style="cyan")
    table.add_column("Overtime Accrued", justify="right", style="yellow")
    table.add_column("Total Month", justify="right", style="bold green")

    table.add_row(
        "Gross Pay",
        f"{cur}{pay.base_monthly_gross:,.2f}",
        f"{cur}{pay.overtime_gross:,.2f}",
        f"{cur}{pay.total_monthly_gross:,.2f}",
    )
    table.add_row(
        "Estimated Income Tax (PAYE)",
        f"-{cur}{pay.base_tax:,.2f}",
        f"-{cur}{pay.overtime_tax:,.2f}",
        f"-{cur}{pay.total_tax:,.2f}",
    )
    table.add_row(
        "National Insurance (Class 1)",
        f"-{cur}{pay.base_ni:,.2f}",
        f"-{cur}{pay.overtime_ni:,.2f}",
        f"-{cur}{pay.total_ni:,.2f}",
    )
    table.add_section()
    table.add_row(
        "[bold]ESTIMATED TAKE-HOME (NET)[/]",
        f"[bold cyan]{cur}{pay.base_net:,.2f}[/]",
        f"[bold yellow]{cur}{pay.overtime_net:,.2f}[/]",
        f"[bold green]{cur}{pay.total_net:,.2f}[/]",
    )

    console.print(table)

    ot_deduction_pct = (
        ((pay.overtime_tax + pay.overtime_ni) / pay.overtime_gross * 100.0)
        if pay.overtime_gross > 0
        else 0.0
    )

    summary_lines = [
        f"• [bold]Annual Base Salary:[/] {cur}{pay.annual_salary:,.2f} ({cur}{pay.base_monthly_gross:,.2f}/mo gross)",
        f"• [bold]Overtime Accrued:[/] {cur}{pay.overtime_gross:,.2f}",
        f"• [bold]Total Estimated Gross:[/] {cur}{pay.total_monthly_gross:,.2f}",
        f"• [bold]Total Deductions:[/] -{cur}{(pay.total_tax + pay.total_ni):,.2f} "
        f"(Tax: {cur}{pay.total_tax:,.2f} | NI: {cur}{pay.total_ni:,.2f})",
        "",
        f"💵  [bold green]ESTIMATED TAKE-HOME PAY: {cur}{pay.total_net:,.2f}[/]",
        f"    ├─ Base Salary Net: [cyan]{cur}{pay.base_net:,.2f}[/]",
        f"    └─ Overtime In-Pocket: [bold yellow]{cur}{pay.overtime_net:,.2f}[/] "
        f"[dim]({ot_deduction_pct:.1f}% effective tax/NI deduction)[/dim]",
        "",
        "[dim]Note: Estimated using standard UK PAYE & Class 1 National Insurance rates (England/Wales/NI).[/dim]",
        "[dim]Excludes voluntary pension contributions, student loans, or custom tax codes.[/dim]",
    ]

    console.print(
        Panel(
            "\n".join(summary_lines),
            title=f"[bold green]💰 ESTIMATED TAKE-HOME PAY — {period_label}[/]",
            border_style="green",
        )
    )
