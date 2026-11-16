# autoharvest 🚜

**autoharvest** is a CLI tool that syncs acknowledged PagerDuty incidents directly to Harvest time entries.

When you finish an on-call shift or want to log your weekly on-call hours, `autoharvest` fetches all alerts you acknowledged during that period and creates corresponding time entries in Harvest—with built-in deduplication so running it multiple times is always safe.

---

## Features

- **Accurate Acknowledged Alert Tracking**: Queries PagerDuty's incident audit trail (`/log_entries`) to find all incidents you personally acknowledged, rather than only open or currently assigned ones.
- **Sociable vs. Unsociable Hours Windowing**:
  - **Sociable Hours**: `08:30 to 00:00` (Weekends) & `17:30 to 00:00` (Weekdays). Alerts during sociable hours are booked in **0.5-hour chunks** with a 30-minute suppression window.
  - **Unsociable Hours**: All other on-call times (e.g. overnight `00:00 to 08:30` on weekends, `00:00 to 17:30` on weekdays). Alerts during unsociable hours are booked in **1.0-hour chunks** with a 60-minute suppression window.
  - Any subsequent alerts that occur within an active window are automatically suppressed/ignored. The next bookable time starts with the next alert that occurs after the active window expires.
- **Scheduled On-Call Shift Filtering**: Automatically matches alerts against your PagerDuty on-call shifts (`/oncalls`). Daytime alerts during normal office hours or days you are not scheduled on-call are excluded by default.
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
| `autoharvest overtime` | Calculate earned overtime earnings and hours for a month or period. |
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
- `--sociable-hours <FLOAT>`: Override hours per alert for sociable hours (default: 0.5).
- `--unsociable-hours <FLOAT>`: Override hours per alert for unsociable hours (default: 1.0).
- `--hours-per-alert <FLOAT>`: Flat override for all alerts (bypasses sociable/unsociable rates).
- `--allow-overlap`: Log every slot as its own entry even when that time is already logged on the same task. By default, an alert that falls in time you've already logged (for example a ticket entered by hand) is added as a new line in that entry's notes instead, and only the uncovered part of a slot gets a new entry.
- `--align-to-clock` / `--from-alert-time`: Book each alert as the clock hour/half-hour it falls in (an alert at 04:37 is logged as 04:00–05:00, and other alerts in that hour are ignored), or start at the alert time (the default). Set `ALIGN_TO_CLOCK=true` in `.env` (setup asks about this, or run `autoharvest config set align-to-clock true`) to make clock-aligned the default.
- `--project-id <INT>`: Override Harvest Project ID.
- `--task-id <INT>`: Override Harvest Task ID.
- `-y`, `--apply`: Automatically submit pending entries without interactive prompt.
- `--dry-run`: Preview changes only (never prompt to apply).
- `--config <PATH>`: Use a custom `.env` file path.

### `overtime` Options & Usage

Calculate total earned overtime from your logged Harvest timesheets.

#### Payday Cycle Rule
By default, `autoharvest overtime` calculates earnings aligned with the company payroll schedule:
- **1st half of the month (Days 1–15)**: Paid in that same month's payday.
- **2nd half of the month (Days 16–end)**: Paid in the *next* month's payday (e.g. a shift on 23 November is paid in the end of December payday).
- **Pay period for end-of-month $M$ payday**: Runs from **$(M-1)\text{-}16$ through $M\text{-}15$** (e.g. End of November Payday covers 16 October – 15 November).

```bash
# Calculate overtime accrued for the next coming payday (default)
autoharvest overtime

# Calculate overtime for the previous payday cycle
autoharvest overtime --last-payday

# Calculate overtime for a specific payday month (e.g. End of November payday: 16 Oct – 15 Nov)
autoharvest overtime --payday November
autoharvest overtime -p 2026-11

# Calculate overtime using calendar months instead of payday cycles (e.g. 1st–31st)
autoharvest overtime --calendar
autoharvest overtime --calendar --month 2026-10

# Show itemized table of all individual time entries
autoharvest overtime --detailed

# View estimated take-home pay and tax deductions (using configured ANNUAL_SALARY)
autoharvest overtime --pay

# Override annual gross salary on the fly
autoharvest overtime --salary 48000

# Custom date range
autoharvest overtime --since 2026-09-01 --until 2026-09-30
```

#### Estimated Take-Home Pay & Tax Deductions
With `--pay` (or `--salary <AMOUNT>`), `autoharvest` calculates estimated monthly take-home pay under standard UK PAYE and Class 1 National Insurance rules:
- **Base Salary Breakdown**: Monthly gross, estimated Income Tax, National Insurance, and net salary.
- **Overtime Marginal Deductions**: Exact tax and NI deducted specifically from the overtime earnings, plus net overtime in-pocket and effective deduction rate.
- **Total Month Reconciled**: Overall gross, total deductions, and estimated net paycheck for the coming payday.

To configure your annual gross salary permanently, run `autoharvest setup` (Step 6) or add `ANNUAL_SALARY=48000` to `.env`.

#### Hourly Rates
- **1st On Call - Active**: £30.00 / hr (override with `--rate-active <FLOAT>` or `RATE_ACTIVE` in `.env`)
- **1st On Call - Inactive**: £6.00 / hr (override with `--rate-inactive <FLOAT>` or `RATE_INACTIVE` in `.env`)
- **Out of hours configuration**: £30.00 / hr (override with `--rate-ooh <FLOAT>` or `RATE_OUT_OF_HOURS` in `.env`)

---

## Changing a Setting

`autoharvest config set <setting> <value>` changes one setting in your `.env` without re-running setup, and prints the old and new value. It honours `--config`.

```bash
autoharvest config set align-to-clock true
autoharvest config set timezone Europe/London
autoharvest config show   # API tokens are hidden
```

Settings: `align-to-clock`, `sociable-hours`, `unsociable-hours`, `project-id`, `task-id`, `timezone`. Values are checked before anything is written. API tokens can only be changed through `autoharvest setup`, which hides them as you type, so they never end up in your shell history.

## How It Works

1. **PagerDuty On-Call Schedule Verification**: Queries `GET /oncalls` to retrieve all scheduled on-call shifts for you during the requested timeframe. Any alerts you acknowledged during normal working hours or weeks off are automatically ignored.
2. **PagerDuty Log Entries**: Queries `GET /log_entries?is_overview=true` in 1-day chunks, filtering for entries where `type == "acknowledge_log_entry"` and `agent.id == YOUR_USER_ID`.
3. **Sociable vs. Unsociable Windowing**:
   - **Sociable Hours**: Weekends (08:30 to 00:00) & Weekdays (17:30 to 00:00). Initiates a **0.5-hour bookable window** (0.50 hours). Alerts within the 30-minute window are ignored.
   - **Unsociable Hours**: All other times / overnight. Initiates a **1.0-hour bookable window** (1.00 hour). Alerts within the 60-minute window are ignored.
   - The next bookable time starts with the next alert that occurs after the active window expires.
   - With `--align-to-clock`, the window is instead the clock-aligned hour (unsociable) or half-hour (sociable) the alert falls in: an alert at 04:37 books 04:00–05:00, and one at 19:44 on a weekend books 19:30–20:00.
4. **Harvest Pre-Check**: Queries `GET /v2/time_entries` for the same date window. It checks for incident matches via `external_reference.id` and note prefix (`PG#<IncidentNo.>`).
5. **Time Already Logged**: Before booking a slot, compares it with your existing entries on the same project and task (other tasks, such as inactive on-call, are ignored).
   - **Fully covered** (e.g. a ticket logged 01:00–02:30 and an alert slot of 01:00–02:00): no new entry. The alert's `PG#…` line is added as a new line in that entry's notes, so it counts as logged on later runs. The entry's time and hours are not changed.
   - **Partly covered** (slot 02:00–03:00, ticket ending 02:30): the alert line is added to the entry covering the alert time, and a new entry is logged for the uncovered part only (02:30–03:00).
   - Entries without start and end times (duration-only accounts) can't be compared and are ignored. Use `--allow-overlap` to turn this off.
6. **Time Entry Creation**: For any unlogged bookable alert, creates an entry in Harvest via `POST /v2/time_entries` with:
   - `spent_date`: Date the alert occurred (in your local timezone)
   - `hours`: 0.5 or 1.0 hours (based on sociable vs unsociable hours)
   - `notes`: `PG#<IncidentNo.> - <Impacted Service> - <Summary>` (e.g. `PG#267540 - Webservers - SSL Certificate for www.sohonet.com due to expire soon`)
   - `external_reference`: `{ id: "PT4KHLK", permalink: "..." }`

---

## Running Tests

Unit tests mock external HTTP requests and verify parsing, deduplication, timezone handling, and CLI execution:

```bash
pytest
```
