# autoharvest 🚜

**autoharvest** is a CLI tool that syncs acknowledged PagerDuty incidents directly to Harvest time entries.

When you finish an on-call shift or want to log your weekly on-call hours, `autoharvest` fetches all alerts you acknowledged during that period and creates corresponding time entries in Harvest—with built-in deduplication so running it multiple times is always safe.

---

## Features

- **Accurate Acknowledged Alert Tracking**: Queries PagerDuty's incident audit trail (`/log_entries`) to find all incidents you personally acknowledged, rather than only open or currently assigned ones.
- **1-Hour Bookable Windows**: Each initial alert creates 1 hour of bookable time. Any alerts that occur within that hour are automatically ignored/suppressed. The next bookable hour starts with the next alert that occurs after the end of the previous window.
- **Tableau-Compatible Naming Convention**: Formats time entry notes strictly as required for Tableau analytics:
  `PG#<IncidentNo.> - <Impacted Service> - <Summary>` (e.g. `PG#267540 - Webservers - SSL Certificate for www.sohonet.com due to expire soon`).
- **Duplicate Prevention (100% Idempotent)**: Checks existing Harvest time entries using `PG#<IncidentNo.>` tags and external reference links. Running the script multiple times will never double-count hours.
- **Safe Dry-Run by Default**: Shows a rich terminal table with incident numbers, titles, services, and timestamps before prompting to create entries.
- **Shift Auto-Detection**: Use `--shift` to automatically look up your most recent on-call shift window from PagerDuty.
- **Interactive Setup**: Run `autoharvest setup` to configure your tokens, test connections, and auto-detect your Harvest on-call task (*Active On-Call time related to non-customer impacting Pagerduty alerts*).

---

## Quickstart

### 1. Requirements & Setup

`autoharvest` requires Python 3.10+.

```bash
git clone https://github.com/olivercampbell/autoharvest.git
cd autoharvest

# Create virtual environment and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

*(Note: `./autoharvest.py` automatically detects and uses the local `.venv`, so you can also run `./autoharvest.py` directly without activating).*

### 2. Configure Credentials

Run the interactive wizard:

```bash
autoharvest setup
```

The wizard will:
1. Ask for your **PagerDuty REST API Token** (from your PagerDuty Profile -> User Settings -> Create API User Token, or an account token).
2. Ask for your **Harvest Personal Access Token** and **Harvest Account ID** (generated at [id.getharvest.com/developers](https://id.getharvest.com/developers)).
3. Fetch your available Harvest projects and tasks and let you pick which one to log on-call hours against (e.g. "Infrastructure & SRE" -> "On-Call Support").
4. Ask for your preferred hours per alert (default: `1.0`).
5. Save your settings to a local `.env` file.

Alternatively, copy `.env.example` to `.env` and fill in the values manually.

### 3. Verify Connection

```bash
autoharvest test
```

Verifies authentication with both PagerDuty and Harvest and displays your connected user accounts.

---

## Usage

### Sync Alerts (Dry-Run Preview)

By default, `autoharvest sync` previews acknowledged alerts from the last 7 days without creating anything in Harvest:

```bash
autoharvest sync
```

You will see a formatted table showing:
- Which alerts you acknowledged
- Date, time, incident number, title, and service
- Status: `✓ Already Logged` vs `⏳ Ready to Log`
- Total hours to be booked

If there are new alerts to log, it will ask for interactive confirmation:
```
Log 2.00 hours for 2 alert(s) into Harvest now? [y/N]:
```

### Commit Directly to Harvest

To skip the confirmation prompt (e.g. for non-interactive execution or scripts):

```bash
autoharvest sync --apply
# or shorthand:
autoharvest sync -y
```

### Sync by On-Call Shift Window

Auto-detect your most recent on-call shift from PagerDuty and only pull alerts from that shift:

```bash
autoharvest sync --shift
```

### Custom Date Ranges

Specify explicit dates or lookback days:

```bash
# Look back past 14 days
autoharvest sync --days 14

# Specific date range
autoharvest sync --since 2026-10-01 --until 2026-10-08

# With hours override (e.g. 0.5 hours per alert)
autoharvest sync --shift --hours-per-alert 0.5 --apply
```

---

## Command Reference

| Command | Description |
| :--- | :--- |
| `autoharvest sync` | Scan acknowledged alerts and sync to Harvest. |
| `autoharvest setup` | Interactive wizard to configure `.env`. |
| `autoharvest test` | Test API connections and display authenticated users. |
| `autoharvest list-projects` | List available Harvest projects and tasks with their IDs. |
| `autoharvest shifts` | View your recent and upcoming PagerDuty on-call shifts. |

### `sync` Options

- `--days <N>`: Look back N days (default: 7).
- `--shift`: Auto-detect and sync only your most recent on-call shift window.
- `--since <DATE>`: Start date/time (ISO 8601 or `YYYY-MM-DD`).
- `--until <DATE>`: End date/time (ISO 8601 or `YYYY-MM-DD`).
- `--show-non-oncall`: Include daytime / non-oncall alerts in the preview table (marked as ignored).
- `--all-alerts`: Process all acknowledged alerts without filtering to scheduled on-call shifts.
- `--hours-per-alert <FLOAT>`: Override hours per alert (default from `.env` or 1.0).
- `--project-id <INT>`: Override Harvest Project ID.
- `--task-id <INT>`: Override Harvest Task ID.
- `-y`, `--apply`: Automatically submit pending entries without interactive prompt.
- `--dry-run`: Preview changes only (never prompt to apply).
- `--config <PATH>`: Use a custom `.env` file path.

---

## How It Works

1. **PagerDuty On-Call Schedule Verification**: Queries `GET /oncalls` to retrieve all scheduled on-call shifts for you during the requested timeframe. Any alerts you acknowledged during normal working hours or weeks off are automatically ignored.
2. **PagerDuty Log Entries**: Queries `GET /log_entries?is_overview=true` in 1-day chunks, filtering for entries where `type == "acknowledge_log_entry"` and `agent.id == YOUR_USER_ID`.
3. **1-Hour Windowing**:
   - The first alert during an on-call shift initiates a 1-hour bookable window (1.0 hour).
   - Any subsequent alerts that happen within that 1-hour window are ignored (0.0 hours).
   - The next bookable hour starts with the next alert that occurs after the end of the previous bookable hour.
4. **Harvest Pre-Check**: Queries `GET /v2/time_entries` for the same date window. It checks for incident matches via `external_reference.id` and note prefix (`PG#<IncidentNo.>`).
5. **Time Entry Creation**: For any unlogged bookable alert, creates an entry in Harvest via `POST /v2/time_entries` with:
   - `spent_date`: Date the alert occurred (in your local timezone)
   - `hours`: 1.0 hour
   - `notes`: `PG#<IncidentNo.> - <Impacted Service> - <Summary>` (e.g. `PG#267540 - Webservers - SSL Certificate for www.sohonet.com due to expire soon`)
   - `external_reference`: `{ id: "PT4KHLK", permalink: "..." }`

---

## Running Tests

Unit tests mock external HTTP requests and verify parsing, deduplication, timezone handling, and CLI execution:

```bash
pytest
```
