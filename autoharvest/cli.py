from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
import click
from rich.prompt import Confirm, FloatPrompt, Prompt

from autoharvest.config import Config, get_default_timezone, load_config, save_config_to_env
from autoharvest.display import (
    console,
    display_projects_table,
    display_shifts_table,
    display_sync_plan_table,
    display_user_status,
    print_banner,
)
from autoharvest.harvest import HarvestAPIError, HarvestClient
from autoharvest.pagerduty import PagerDutyAPIError, PagerDutyClient
from autoharvest.sync import (
    create_sync_plan,
    execute_sync_plan,
    parse_datetime,
    resolve_shift_window,
)


@click.group()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False),
    help="Path to custom .env configuration file.",
)
@click.pass_context
def cli(ctx: click.Context, config_path: Optional[str]) -> None:
    """autoharvest - Sync PagerDuty acknowledged incidents to Harvest time entries."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config_path)


@cli.command("test")
@click.pass_context
def test_cmd(ctx: click.Context) -> None:
    """Test connections to PagerDuty and Harvest APIs."""
    print_banner()
    cfg: Config = ctx.obj["config"]

    pd_errors = cfg.validate_pagerduty()
    harvest_errors = cfg.validate_harvest()

    if pd_errors or harvest_errors:
        console.print("[bold red]Configuration errors found:[/]")
        for err in pd_errors + harvest_errors:
            console.print(f"  [red]• {err}[/]")
        console.print("\nRun [bold green]autoharvest setup[/] to configure your credentials.")
        raise click.Abort()

    console.print("\n[dim]Testing API connections...[/dim]")

    # Test PagerDuty
    pd_client = PagerDutyClient(token=cfg.pagerduty_api_token, base_url=cfg.pagerduty_base_url)
    try:
        if cfg.pagerduty_user_id:
            pd_user = pd_client.get_user_by_id(cfg.pagerduty_user_id)
        else:
            pd_user = pd_client.get_current_user()
    except PagerDutyAPIError as e:
        console.print(f"[bold red]PagerDuty Connection Failed:[/] {e}")
        raise click.Abort()

    # Test Harvest
    harvest_client = HarvestClient(
        access_token=cfg.harvest_access_token,
        account_id=cfg.harvest_account_id,
        user_agent=cfg.harvest_user_agent or f"autoharvest ({pd_user.email})",
    )
    try:
        harvest_user = harvest_client.get_current_user()
    except HarvestAPIError as e:
        console.print(f"[bold red]Harvest Connection Failed:[/] {e}")
        raise click.Abort()

    console.print("\n[bold green]✓ Connections Successful![/]\n")
    display_user_status(pd_user, harvest_user)

    if cfg.harvest_project_id and cfg.harvest_task_id:
        console.print(
            f"\n[bold]Configured Harvest Destination:[/] Project [cyan]{cfg.harvest_project_id}[/], Task [green]{cfg.harvest_task_id}[/]"
        )
    else:
        console.print(
            "\n[yellow]Notice:[/] Project ID and/or Task ID not configured. Run [bold]autoharvest setup[/] or [bold]autoharvest list-projects[/]."
        )


@cli.command("list-projects")
@click.pass_context
def list_projects_cmd(ctx: click.Context) -> None:
    """List available Harvest projects and tasks assigned to your user."""
    print_banner()
    cfg: Config = ctx.obj["config"]

    errors = cfg.validate_harvest()
    if errors:
        for err in errors:
            console.print(f"[red]• {err}[/]")
        raise click.Abort()

    harvest_client = HarvestClient(
        access_token=cfg.harvest_access_token,
        account_id=cfg.harvest_account_id,
        user_agent=cfg.harvest_user_agent or "autoharvest",
    )
    try:
        projects = harvest_client.get_project_assignments()
    except HarvestAPIError as e:
        console.print(f"[bold red]Failed to fetch projects:[/] {e}")
        raise click.Abort()

    if not projects:
        console.print("[yellow]No project assignments found for this Harvest user.[/]")
        return

    display_projects_table(projects)


@cli.command("shifts")
@click.option("--days", default=30, show_default=True, help="Number of past days to check.")
@click.pass_context
def shifts_cmd(ctx: click.Context, days: int) -> None:
    """List your recent and upcoming on-call shifts from PagerDuty."""
    print_banner()
    cfg: Config = ctx.obj["config"]

    errors = cfg.validate_pagerduty()
    if errors:
        for err in errors:
            console.print(f"[red]• {err}[/]")
        raise click.Abort()

    pd_client = PagerDutyClient(token=cfg.pagerduty_api_token, base_url=cfg.pagerduty_base_url)
    try:
        if cfg.pagerduty_user_id:
            pd_user = pd_client.get_user_by_id(cfg.pagerduty_user_id)
        else:
            pd_user = pd_client.get_current_user()
    except PagerDutyAPIError as e:
        console.print(f"[bold red]PagerDuty Error:[/] {e}")
        raise click.Abort()

    tz = cfg.get_tz()
    now = datetime.now(tz)
    since = now - timedelta(days=days)
    until = now + timedelta(days=14)

    try:
        shifts = pd_client.get_oncall_shifts(user_id=pd_user.id, since=since, until=until)
    except PagerDutyAPIError as e:
        console.print(f"[bold red]Failed to fetch shifts:[/] {e}")
        raise click.Abort()

    if not shifts:
        console.print(f"[yellow]No on-call shifts found for user {pd_user.name} ({pd_user.id}).[/]")
        return

    display_shifts_table(shifts, current_user_id=pd_user.id)


@cli.command("setup")
@click.pass_context
def setup_cmd(ctx: click.Context) -> None:
    """Interactive wizard to configure autoharvest (.env)."""
    print_banner()
    console.print("\n[bold cyan]Welcome to autoharvest interactive setup![/]")
    console.print("This wizard will help you configure your PagerDuty and Harvest credentials.\n")

    cfg: Config = ctx.obj["config"]

    # 1. PagerDuty Token
    pd_token = Prompt.ask(
        "Enter your [bold]PagerDuty REST API Token[/] (user or account token)",
        default=cfg.pagerduty_api_token or "",
        password=True,
    )
    if not pd_token:
        console.print("[red]PagerDuty token is required.[/]")
        raise click.Abort()

    console.print("[dim]Verifying PagerDuty token...[/dim]")
    pd_client = PagerDutyClient(token=pd_token)
    pd_user_id = cfg.pagerduty_user_id
    try:
        pd_user = pd_client.get_current_user()
        console.print(f"[green]✓ Authenticated as:[/] {pd_user.name} ({pd_user.email}) [ID: {pd_user.id}]")
        pd_user_id = pd_user.id
    except PagerDutyAPIError:
        console.print("[yellow]Could not fetch profile via /users/me (likely an account-level token).[/]")
        pd_user_id = Prompt.ask("Enter your PagerDuty User ID (e.g. P123456)", default=pd_user_id or "")

    # 2. Harvest Token & Account ID
    harvest_token = Prompt.ask(
        "\nEnter your [bold]Harvest Personal Access Token[/] (from id.getharvest.com/developers)",
        default=cfg.harvest_access_token or "",
        password=True,
    )
    harvest_account_id = Prompt.ask(
        "Enter your [bold]Harvest Account ID[/] (integer string from id.getharvest.com/developers)",
        default=cfg.harvest_account_id or "",
    )

    console.print("[dim]Verifying Harvest credentials...[/dim]")
    harvest_client = HarvestClient(
        access_token=harvest_token,
        account_id=harvest_account_id,
        user_agent=f"autoharvest ({pd_user_id})",
    )
    try:
        h_user = harvest_client.get_current_user()
        console.print(f"[green]✓ Authenticated as:[/] {h_user.full_name} ({h_user.email})")
    except HarvestAPIError as e:
        console.print(f"[bold red]Harvest authentication failed:[/] {e}")
        raise click.Abort()

    # 3. Project & Task selection
    console.print("\n[dim]Fetching your Harvest projects and tasks...[/dim]")
    projects = harvest_client.get_project_assignments()
    if projects:
        display_projects_table(projects)
    else:
        console.print("[yellow]No assigned projects found.[/]")

    proj_default = str(cfg.harvest_project_id) if cfg.harvest_project_id else ""
    task_default = str(cfg.harvest_task_id) if cfg.harvest_task_id else ""

    # Auto-detect target task for on-call (e.g. Active On-Call time related to non-customer impacting Pagerduty alerts)
    target_needle = "non-customer impacting pagerduty alerts"
    found_recommended = False
    for p in projects:
        for t in p.tasks:
            if target_needle in t.name.lower() or "active on-call" in t.name.lower():
                if not proj_default:
                    proj_default = str(p.id)
                if not task_default:
                    task_default = str(t.id)
                console.print(
                    f"\n[bold green]★ Recommended Task Found:[/] [cyan]{p.name}[/] (ID: {p.id}) → "
                    f"[green]{t.name}[/] (ID: {t.id})"
                )
                found_recommended = True
                break
        if found_recommended:
            break

    chosen_proj_id = Prompt.ask(
        "\nEnter the [bold cyan]Project ID[/] to log on-call hours against",
        default=proj_default,
    )
    chosen_task_id = Prompt.ask(
        "Enter the [bold green]Task ID[/] to log on-call hours against",
        default=task_default,
    )

    # 4. Hours per alert
    hours_per_alert = FloatPrompt.ask(
        "\nEnter [bold]hours to log per acknowledged alert[/]",
        default=cfg.hours_per_alert or 1.0,
    )

    # 5. Timezone
    detected_tz = cfg.timezone or get_default_timezone()
    chosen_tz = Prompt.ask("\nEnter [bold]Timezone[/] for date grouping", default=detected_tz)

    # Save to .env
    env_updates = {
        "PAGERDUTY_API_TOKEN": pd_token,
        "PAGERDUTY_USER_ID": pd_user_id or "",
        "HARVEST_ACCESS_TOKEN": harvest_token,
        "HARVEST_ACCOUNT_ID": harvest_account_id,
        "HARVEST_PROJECT_ID": chosen_proj_id,
        "HARVEST_TASK_ID": chosen_task_id,
        "HARVEST_USER_AGENT": f"autoharvest ({h_user.email})",
        "HOURS_PER_ALERT": str(hours_per_alert),
        "TIMEZONE": chosen_tz,
    }

    env_path = save_config_to_env(env_updates)
    console.print(f"\n[bold green]✓ Configuration saved to {env_path.resolve()}[/]")
    console.print("\nYou are all set! Run [bold cyan]autoharvest sync[/] to preview your alerts.")


@cli.command("sync")
@click.option("--since", default=None, help="Start time/date (ISO 8601 or YYYY-MM-DD).")
@click.option("--until", default=None, help="End time/date (ISO 8601 or YYYY-MM-DD).")
@click.option("--days", default=7, show_default=True, help="Days to look back (used if --since is omitted).")
@click.option("--shift", is_flag=True, default=False, help="Auto-detect most recent on-call shift window.")
@click.option("--hours-per-alert", type=float, default=None, help="Override hours to log per alert.")
@click.option("--project-id", type=int, default=None, help="Override Harvest Project ID.")
@click.option("--task-id", type=int, default=None, help="Override Harvest Task ID.")
@click.option("--apply", "-y", "apply_changes", is_flag=True, default=False, help="Submit changes to Harvest directly.")
@click.option("--dry-run", is_flag=True, default=False, help="Preview without making any changes.")
@click.pass_context
def sync_cmd(
    ctx: click.Context,
    since: Optional[str],
    until: Optional[str],
    days: int,
    shift: bool,
    hours_per_alert: Optional[float],
    project_id: Optional[int],
    task_id: Optional[int],
    apply_changes: bool,
    dry_run: bool,
) -> None:
    """Sync acknowledged PagerDuty alerts to Harvest time tracking."""
    print_banner()
    cfg: Config = ctx.obj["config"]

    # Overrides from CLI flags
    effective_project_id = project_id or cfg.harvest_project_id
    effective_task_id = task_id or cfg.harvest_task_id
    effective_hours = hours_per_alert if hours_per_alert is not None else cfg.hours_per_alert

    # Validate prerequisites
    errors = cfg.validate_pagerduty() + cfg.validate_harvest()
    if not effective_project_id:
        errors.append("Harvest Project ID is not specified. Set HARVEST_PROJECT_ID in .env or pass --project-id.")
    if not effective_task_id:
        errors.append("Harvest Task ID is not specified. Set HARVEST_TASK_ID in .env or pass --task-id.")

    if errors:
        for err in errors:
            console.print(f"[bold red]• {err}[/]")
        console.print("\nRun [bold green]autoharvest setup[/] to configure.")
        raise click.Abort()

    tz = cfg.get_tz()
    pd_client = PagerDutyClient(token=cfg.pagerduty_api_token, base_url=cfg.pagerduty_base_url)

    # Determine PagerDuty user
    try:
        if cfg.pagerduty_user_id:
            pd_user = pd_client.get_user_by_id(cfg.pagerduty_user_id)
        else:
            pd_user = pd_client.get_current_user()
    except PagerDutyAPIError as e:
        console.print(f"[bold red]Failed to get PagerDuty user:[/] {e}")
        raise click.Abort()

    harvest_client = HarvestClient(
        access_token=cfg.harvest_access_token,
        account_id=cfg.harvest_account_id,
        user_agent=cfg.harvest_user_agent or f"autoharvest ({pd_user.email})",
    )

    # Determine date range
    now = datetime.now(tz)
    if shift:
        console.print("[dim]Looking up your most recent on-call shift from PagerDuty...[/dim]")
        try:
            start_dt, end_dt, chosen_shift = resolve_shift_window(pd_client, pd_user.id, tz)
            console.print(
                f"[green]Found shift:[/] {chosen_shift.schedule_summary or 'Shift'} "
                f"({start_dt.strftime('%Y-%m-%d %H:%M')} to {end_dt.strftime('%Y-%m-%d %H:%M %Z')})"
            )
        except Exception as e:
            console.print(f"[bold red]Error resolving shift:[/] {e}")
            raise click.Abort()
    elif since:
        start_dt = parse_datetime(since, tz)
        end_dt = parse_datetime(until, tz) if until else now
    else:
        start_dt = now - timedelta(days=days)
        end_dt = now

    console.print(
        f"[dim]Scanning alerts acknowledged by [bold]{pd_user.name}[/] "
        f"from {start_dt.strftime('%Y-%m-%d %H:%M')} to {end_dt.strftime('%Y-%m-%d %H:%M %Z')}...[/dim]"
    )

    try:
        plan = create_sync_plan(
            pd_client=pd_client,
            harvest_client=harvest_client,
            user_id=pd_user.id,
            since=start_dt,
            until=end_dt,
            project_id=effective_project_id,
            task_id=effective_task_id,
            hours_per_alert=effective_hours,
            tz=tz,
        )
    except Exception as e:
        console.print(f"[bold red]Sync analysis failed:[/] {e}")
        raise click.Abort()

    if not plan.items:
        console.print(
            f"\n[green]✓ No alerts were acknowledged by {pd_user.name} during this period.[/]"
        )
        return

    # If explicit dry-run or no apply flag
    is_dry_run = dry_run or (not apply_changes)
    display_sync_plan_table(plan, is_dry_run=is_dry_run)

    if not plan.pending_items:
        console.print("\n[bold green]✓ All acknowledged alerts for this period are already logged in Harvest![/]")
        return

    # If user passed --dry-run explicitly, we are done
    if dry_run:
        console.print("\n[bold yellow]Dry-run complete. No changes were made.[/]")
        console.print("Run with [bold green]--apply[/] or [bold green]-y[/] to create these time entries in Harvest.")
        return

    # If --apply was not provided, prompt user interactively
    if not apply_changes:
        console.print("")
        should_apply = Confirm.ask(
            f"Log [bold green]{plan.total_pending_hours:.2f} hours[/] "
            f"for [bold green]{len(plan.pending_items)} alert(s)[/] into Harvest now?"
        )
        if not should_apply:
            console.print("[dim]Aborted. No changes made.[/dim]")
            return

    # Execute changes
    console.print("\n[bold cyan]Submitting time entries to Harvest...[/]")
    results = execute_sync_plan(harvest_client, plan)

    # Re-display results table with created/failed statuses
    display_sync_plan_table(plan, is_dry_run=False)

    failed = [item for item in results if item.error_message]
    if failed:
        console.print(f"\n[bold red]⚠️ {len(failed)} entry/entries failed to create:[/]")
        for f in failed:
            console.print(f"  • Incident #{f.incident.incident_number}: {f.error_message}")
    else:
        console.print("\n[bold green]✓ Successfully logged all pending alerts to Harvest![/]")


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()
